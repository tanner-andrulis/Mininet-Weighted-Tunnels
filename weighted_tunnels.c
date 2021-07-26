#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <netinet/in.h>
#include <linux/types.h>
#include <linux/netfilter.h>		/* for NF_ACCEPT */
#include <linux/ip.h>
#include <string.h>
#include <argp.h>
#include <stdbool.h>
#include <pthread.h>

#include <libnetfilter_queue/libnetfilter_queue.h>
#include <libnetfilter_queue/pktbuff.h>
#include <libnetfilter_queue/libnetfilter_queue_ipv4.h>
#include <libnetfilter_queue/libnetfilter_queue_tcp.h>
#include <libnetfilter_queue/libnetfilter_queue_udp.h>
#include <netinet/tcp.h>
#include <netinet/udp.h>


// =================================================================================================
// GLOBAL VARIABLES
// =================================================================================================
// MAKE SURE THESE ARE THE SAME AS IN mod_ports.py
// Weights / cur allocs
#define MAX_TUNNELS_PER_FLOW 16
#define MAX_FLOWS 128
double weights[MAX_FLOWS][MAX_TUNNELS_PER_FLOW];
double curr_allocs[MAX_FLOWS][MAX_TUNNELS_PER_FLOW];

// For message parsing
// Assuming at most 32 characters per flow
#define MAX_WEIGHT_MESSAGE_SIZE MAX_TUNNELS_PER_FLOW*MAX_FLOWS*32
int weight_ready = 0;
char message_buff[MAX_WEIGHT_MESSAGE_SIZE + 1];
double weights_in_progress[MAX_FLOWS][MAX_TUNNELS_PER_FLOW];


#define QUEUE_MAXLEN 65536 // 64k
#define RECV_BUF_SIZE 16777216 // 16MB

#define FAIL(msg) {fprintf(stderr, msg); return -1;}

// =================================================================================================
// USER ARGS
// =================================================================================================
unsigned int my_ip = 0;
unsigned short recv_start_port = 10000;
unsigned short send_start_port = 20000;
int verbose = 0;
int calc_checksum = 0;
unsigned short queue_num = 58;
char* weight_file = NULL;

// =================================================================================================
// GLOBAL VARIABLES
// =================================================================================================
struct pkt_buff * pktb;

// =================================================================================================
// PARSING USER ARGS
// =================================================================================================
int check_numeric_input(long int min, long int max, char* errstr)
{
	// Checks user input is in the range [min, max] inclusive. On fail, prints the
	// errstr and exits with error code -1
	extern char *optarg;
	long int val = strtol(optarg, NULL, 10);
	if(errno || val < min || val > max) {
		printf(errstr, optarg);
		printf("Please provide a value between %ld and %ld inclusive.\n", min, max);
		exit(-1);
	}
	return val;
}

int parse_args(int argc, char **argv) {
	// Parses command line arguments
	int c;
	extern char *optarg;
	while ((c = getopt(argc, argv, "i:w:r:s:q:cvh")) != -1) {
		switch (c) {
		case 'i':
			my_ip = (unsigned int) check_numeric_input(1L, 2147483647L, "Invalid integer for -i option: %s. IP should be given as an integer.\n");
			break;
		case 'w':
			weight_file = optarg;
			break;
		case 'r':
			recv_start_port = (unsigned short) check_numeric_input(1L, 65535L, "Invalid integer for -s option: %s\n");
			break;
		case 's':
			send_start_port = (unsigned short) check_numeric_input(1L, 65535L, "Invalid integer for -r option: %s\n");
			break;
		case 'q':
			queue_num = (unsigned short) check_numeric_input(0L, 255L, "Invalid integer for -q option: %s\n");
			break;
		case 'c':
			calc_checksum = 1;
			break;
		case 'v':
			verbose = 1;
			break;
		case 'h':
			printf("%s: TCP & UDP Port Spoofer\n", argv[0]);
			printf("Usage: %s -i my_ip [-r recv_start_port] [-s send_start_port] [-q queue_num] [-c calc_checksum] [-v]\n", argv[0]);
			printf("\n");
			printf("Options:\n");
			printf("  -i my_ip=my_ip                Required. IP address of this device formatted as an integer.\n");
			printf("  -w weight_file=path           File with port weights. If it exists, will be read then deleted. Checked once per second.\n");
			printf("  -r recv_start_port=recv_start_port     The minimum port for iperf receivers. Set to: %d\n", recv_start_port);
			printf("  -r send_start_port=send_start_port     The minimum port for iperf senders. Set to: %d. Must be > recv_start_port.\n", send_start_port);
			printf("  -q queue_num=queue_num        NFQueue queue number to use. Set to: %d.\n", queue_num);
			printf("  -c calculate_checksum         Calculate checksum for UDP & TCP packets. By default, checksum is set to 0.\n");
			printf("  -v verbose                    Print the results of each packet.\n");
			exit(0);
			break;
		case '?':
			printf("Usage: %s -i my_ip [-r recv_start_port] [-s send_start_port] [-q queue_num] [-c calc_checksum] [-v]\n", argv[0]);
			return -1;
			break;
		}
	}
	if(my_ip == 0)
	{
		printf("Invalid IP and/or port!\n");
		return -1;
	}
	if(((int) recv_start_port) + MAX_FLOWS >= send_start_port)
	{
		printf("Send and recv start port too close together! Send start port must be > recv_start_port + %d.\n", MAX_FLOWS);
		return -1;
	}
	if(((int) send_start_port) + MAX_FLOWS * MAX_TUNNELS_PER_FLOW >= 65535)
	{
		printf("Send start port too high! Send start port must be < 65535 - %d.\n", MAX_TUNNELS_PER_FLOW * MAX_FLOWS);
		return -1;
	}
	if(!weight_file)
	{
		printf("No weight file given!\n");
		return -1;
	}
	printf("Intercepting packets on queue %d.\n", queue_num);
	printf("Source ports %d <= sport <= %d will be modified.\n", send_start_port, send_start_port + MAX_FLOWS * MAX_TUNNELS_PER_FLOW);
	printf("Iperf session from host M to host N should use source port %d + N and destination port %d + M.\n", send_start_port, recv_start_port);
	printf("Packets from IP address %d are outgoing.\n", my_ip);
	printf("	Source port %d + N will be mapped to %d + N * %d + Tunnel #. Destination port unchanged.\n", send_start_port, send_start_port, MAX_TUNNELS_PER_FLOW);
	printf("Other packets are incoming.\n");
	printf("	Source port %d + N * %d + Tunnel # will be mapped to %d + N. Destination port unchanged.\n", send_start_port, MAX_TUNNELS_PER_FLOW, send_start_port);
	printf("Calculate checksum: %d\n", calc_checksum);
	printf("Weight file: %s\n", weight_file);
	printf("Verbose: %d\n", verbose);
	return 0;
}

// =================================================================================================
// MESSAGE PARSING
// =================================================================================================
void parse_weight_message(char* lines[], int line_count)
{
	// Parses a weight message and fills in weights_in_progress
	bzero(weights_in_progress, sizeof(weights_in_progress));

	// Get weights from subsequent lines
	for(int i = 0; i < line_count; i++)
	{
		if(i > MAX_FLOWS)
		{
			printf("Too many lines in file! Can only give %d flows.", MAX_FLOWS);
			exit(-1);
		}
		int j = 0;
		printf("Line %d:\n", i);
		if(!lines[i][0]) continue;
		printf("Line: %s\n", lines[i]);
		char* weight = strtok(lines[i], ",");
		while(j < MAX_TUNNELS_PER_FLOW)
		{
			if(!weight) break;
			weights_in_progress[i][j++] = atof(weight);
			if(verbose) printf("Destination host %d tunnel %d: Weight %lf\n", i, j - 1, weights_in_progress[i][j - 1]);
			weight = strtok(NULL, ",");
		}
		if(strtok(NULL, ","))
		{
			printf("Too many weights in line! Can only give %d weights.", MAX_TUNNELS_PER_FLOW);
			exit(-1);
		}
	}
}

void* read_weights(void * unused)
{
	// Polls the weight file every 100ms. If one is written, reads and deletes
	// it.
	FILE * f;
	while(1)
	{
		// Read weight file
		if(verbose) printf(".\n");
		usleep(100000);
		if(weight_ready || access(weight_file, F_OK)) continue;
		if(!(f = fopen(weight_file, "r"))) continue;
		int nread = fread(message_buff, sizeof(char), MAX_WEIGHT_MESSAGE_SIZE, f);
		message_buff[nread] = '\0';
		fclose(f);
		remove(weight_file);
		if(verbose) printf("Received new weights!\n%s\n", weight_file);
			
		// Split by lines
		char* lines[MAX_FLOWS + 1];
		int line_count = 1;
		lines[0] = message_buff;
		for(int i = 0; i < nread; i++) if(message_buff[i] == '\n')
		{
			message_buff[i] = '\0';
			lines[line_count++] = &(message_buff[i + 1]);
			if(line_count == MAX_FLOWS) break;
		}
		// Parse lines
		parse_weight_message(lines, line_count);
		weight_ready = 1;
	}
}

// =================================================================================================
// PORT TRANSLATION
// =================================================================================================
unsigned short pick_next_bucket(unsigned short dnum)
{
	// Picks a new destination bucket for destination "dnum".

	// Get new weights if available
	if(weight_ready)
	{
		bzero(curr_allocs, sizeof(curr_allocs));
		bcopy(weights_in_progress, weights, sizeof(weights));
		weight_ready = 0;
	}

	// Find next candidate
	double min = 1e+300;
	int min_ind = -1;
	printf("Dnum: %d\n", dnum);
	for(int i = 0; i < MAX_TUNNELS_PER_FLOW; i++)
		if(curr_allocs[dnum][i] < min && weights[dnum][i] > 0)
		{
			min_ind = i;
			min = curr_allocs[dnum][min_ind];
		}
	
	// Put everyone back near 0 so we don't overflow
	for(int i = 0; i < MAX_TUNNELS_PER_FLOW; i++) curr_allocs[dnum][i] -= min;
	// Tax the one picked porportional to inverse of weight
	if(min_ind == -1)
	{
		if(verbose) printf("Buckets to destination %d all have zero weights!\n", dnum);
		return 0;
	}
	curr_allocs[dnum][min_ind] += 1 / weights[dnum][min_ind];
	return min_ind;
}

unsigned short port_translate(unsigned short sport, unsigned int saddr)
{
	// Main port translation function. Modifies a port given a source port
	// and source address.

	if(sport < send_start_port || 
	   sport > ((int) send_start_port) + MAX_FLOWS * MAX_TUNNELS_PER_FLOW)
	   {
		   return sport;
	   }

	// Input rule
	if(saddr != my_ip)
		return ((sport - send_start_port) / MAX_TUNNELS_PER_FLOW) + send_start_port;
	// Output rule
	unsigned short dnum = sport - send_start_port;
	return send_start_port + (unsigned short) pick_next_bucket(dnum) + dnum * MAX_TUNNELS_PER_FLOW;
}

// =================================================================================================
// MAIN LOOP
// =================================================================================================
static int pkt_accept(char * message, struct nfq_q_handle *queue, struct nfqnl_msg_packet_hdr *ph)
{
	// Accepts a packet. If in verbose mode prints the given message.
	if(message && verbose) printf("%s", message);
	return nfq_set_verdict(queue, ntohl(ph->packet_id), NF_ACCEPT, 0, NULL);
}

static int pkt_mangle(struct nfq_q_handle *queue, struct nfgenmsg *nfmsg, struct nfq_data *nfad, void * unused)
{
	// Main callback for nfqueue. Applies source/destination port mangling as
	// described in the -h option.
	// The tutorial "Modifying Network Traffic with NFQUEUE and ARP Spoofing"
	// by Andrew Melnichenko was immensely helpful in getting all of this
	// together:
	//      https://www.apriorit.com/dev-blog/598-linux-mitm-nfqueue

	// Network layer variables and packet buffer
    struct nfqnl_msg_packet_hdr *ph;
	int ip_payload_size;
	unsigned char *packet_buffer;
    struct iphdr * ip_hdr;
	unsigned int saddr;

	// Transport layer variables
    struct tcphdr *tcph;
    struct udphdr *udph;
	unsigned short new_sport;
	unsigned short sport;

	// Initialize pktb
	pktb = NULL;

	// Parse packet. If parse fails at any point beyond getting ID, just accept packet.
	// Get packet header and payload
	if(!(ph = nfq_get_msg_packet_hdr(nfad)))
	{
		fprintf(stderr, "Failed to get packet header.\n");
		return -1;
	}
  	if((ip_payload_size = nfq_get_payload(nfad, &packet_buffer)) < 0)
		return pkt_accept("Failed to get packet payload. Accepting packet.\n", queue, ph);
	
	// Create packet buffer
    if(!(pktb = pktb_alloc(AF_INET, packet_buffer, ip_payload_size, 0)))
		return pkt_accept("Could not allocate packet buffer. Accepting packet.\n", queue, ph);

	// Get IP header and transport header
	if(!(ip_hdr = nfq_ip_get_hdr(pktb))) 
		return pkt_accept("Could not parse IPV4 header. Accepting packet.\n", queue, ph);
    if(nfq_ip_set_transport_header(pktb, ip_hdr) < 0)
		return pkt_accept("Could not parse transport layer header. Accepting packet.\n", queue, ph);
	saddr = ntohl(ip_hdr->saddr);
	// TCP set ports
    if(ip_hdr->protocol == IPPROTO_TCP)
    {
		if(!(tcph = nfq_tcp_get_hdr(pktb)))
			return pkt_accept("Could not parse TCP header. Accepting packet.\n", queue, ph);
		sport = ntohs(tcph->th_sport);
		if(sport == (new_sport = port_translate(sport, saddr)))
			return pkt_accept("Source port unchanged. Accepting packet.\n", queue, ph);
		if(verbose) printf("TCP packet %08X:%d->:%d packet now %08X:%d->:%d\n", saddr, sport, ntohs(tcph->th_dport), saddr, new_sport, ntohs(tcph->th_dport));
		tcph->th_sport=htons(new_sport);
		tcph->check = 0;
		if(calc_checksum) nfq_tcp_compute_checksum_ipv4(tcph, ip_hdr);
        return nfq_set_verdict(queue, ntohl(ph->packet_id), NF_ACCEPT, pktb_len(pktb), pktb_data(pktb));
    }
	// UDP set ports
	if(ip_hdr->protocol == IPPROTO_UDP)
    {
		if(!(udph = nfq_udp_get_hdr(pktb)))
			return pkt_accept("Could not parse UDP header. Accepting packet.\n", queue, ph);
		sport = ntohs(udph->uh_sport);
		if(sport == (new_sport = port_translate(sport, saddr)))
			return pkt_accept("Source port unchanged. Accepting packet.\n", queue, ph);
		if(verbose) printf("UDP packet %08X:%d->:%d packet now %08X:%d->:%d\n", saddr, sport, ntohs(udph->uh_dport), saddr, new_sport, ntohs(udph->uh_dport));
		udph->uh_sport=htons(new_sport);
		udph->check = 0;
		if(calc_checksum) nfq_udp_compute_checksum_ipv4(udph, ip_hdr);
        return nfq_set_verdict(queue, ntohl(ph->packet_id), NF_ACCEPT, pktb_len(pktb), pktb_data(pktb));
    }
	return pkt_accept(NULL, queue, ph);
}

int main(int argc, char **argv)
{
	struct nfq_handle *h;
	struct nfq_q_handle *qh;
	int fd;
	int rv;
	char buf[4096] __attribute__ ((aligned));

	// Parse user args and initialize variables
	if(parse_args(argc, argv))
	{
		fprintf(stderr, "Invalid options. Exiting.\n");
		fprintf(stderr, "%s -h for usage information.\n", argv[0]);
		return -1;
	}
	bzero(weights, sizeof(weights));
	bzero(curr_allocs, sizeof(curr_allocs));

	// Set up NetFilter Queue Handle
	// Setup source code adapted from libnetfilter_queue/utils/nf-queue.c
	printf("Setting up NetFilter Queue Handle.\n");
	int success = 0;
	if(!(h = nfq_open()))
		fprintf(stderr, "Error during nfq_open()\n");
	else if(nfq_bind_pf(h, AF_INET) < 0)
		fprintf(stderr, "Failed to bind queue handler for AF_INET. Error during nfq_bind_pf()\n");
	else if(!(qh = nfq_create_queue(h,  queue_num, &pkt_mangle, NULL)))
		fprintf(stderr, "Error during nfq_create_queue()! Failed to bind socket to queue %d\n", queue_num);
	else if(nfq_set_mode(qh, NFQNL_COPY_PACKET, 0xffff) < 0)
		fprintf(stderr, "Can't set packet_copy mode\n");
	else success = 1;
	if(!success)
	{
		printf("Failed. %s -h for usage information.", argv[0]);
		return -1;
	}

	// Increase speed of process and queue sizes to avoid drops.
	if(nice(-20)) printf("Failed to set process priority!\n");
	nfq_set_queue_maxlen(qh, QUEUE_MAXLEN);
	nfnl_rcvbufsiz(nfq_nfnlh(h), RECV_BUF_SIZE);

	// Start up weight reading thread
    pthread_t id;
	pthread_create(&id, NULL, read_weights, NULL);
	if(!id) FAIL("Failed to spawn weight reading thread.\n");

	printf("Intercepting packets on queue %d.\n", queue_num);

	fd = nfq_fd(h);
	for (;;) {
		if ((rv = recv(fd, buf, sizeof(buf), 0)) >= 0) {
			nfq_handle_packet(h, buf, rv);
			if(pktb != NULL)
			{
				pktb_free(pktb);
				pktb = NULL;
			}
		

			continue;
		}
		if (rv < 0 && errno == ENOBUFS) {
			if(verbose) fprintf(stderr, "Losing packets! See doxygen documentation of netfilter_queue on how to fix.\n");
			continue;
		}
		if(verbose) printf("Packet recv failed.\n");
		break;
	}

	nfq_destroy_queue(qh);
	nfq_close(h);
	return 0;
}

// =================================================================================================
// PORT_LAYOUT
// =================================================================================================
// recv_start_port +  0: Messages from destination 0
// recv_start_port +  1: Messages from destination 1
// recv_start_port +  2: Messages from destination 2
// recv_start_port +  3: Messages from destination 3
// ...
// ...
// send_start_port + 0 * MAX_TUNNELS_PER_FLOW + 0: Destination 0 tunnel 0
// send_start_port + 0 * MAX_TUNNELS_PER_FLOW + 1: Destination 0 tunnel 1
//                 + 0 * MAX_TUNNELS_PER_FLOW + 2: Destination 0 tunnel 2
//                 + 0 * MAX_TUNNELS_PER_FLOW + 3: Destination 0 tunnel 3
// ...
// send_start_port + 1 * MAX_TUNNELS_PER_FLOW - 1: Destination 0 tunnel N
// send_start_port + 1 * MAX_TUNNELS_PER_FLOW + 0: Destination 1 tunnel 0
// send_start_port + 1 * MAX_TUNNELS_PER_FLOW + 1: Destination 1 tunnel 1
// ...
// 
//
//
// e.g. For the following values:
//      recv_start port = 5000
//      send_start_port = 10000
//      MAX_TUNNELS_PER_FLOW 8
//
//      OUTPUT CHAIN
//      Host 0 iperf sessions send out:
//          Host 0 port 10001 -> Host 1 port 5000
//          Host 0 port 10002 -> Host 2 port 5000
//          Host 0 port 10003 -> Host 3 port 5000
//      After going through this daemon, the network receives:
//          Host 1 port 10000-10007 -> Host 0 port 5000
//          Host 2 port 10008-10015 -> Host 0 port 5001
//          Host 3 port 10016-10023 -> Host 0 port 5002
//
//      INPUT CHAIN
//      On the network, for things going to host 0:
//          Host 1 port 10000-10007 -> Host 0 port 5000
//          Host 2 port 10008-10015 -> Host 0 port 5001
//          Host 3 port 10016-10023 -> Host 0 port 5002
//      After going through this daemon, host 0 iperf sessions receive:
//          Host 1 port 10001 -> Host 0 port 5000
//          Host 2 port 10002 -> Host 0 port 5001
//          Host 3 port 10003 -> Host 0 port 5002
//