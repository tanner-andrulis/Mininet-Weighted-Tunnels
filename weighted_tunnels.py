from typing import Tuple, List
from mininet.net import Mininet
import os

# For each host's iperf port modification. Must match weighted_tunnels.c!!
MAX_TUNNELS_PER_FLOW = 16
MAX_FLOWS = 128  # per host!
DEFAULT_RECV_START_PORT = 10000
DEFAULT_SEND_START_PORT = 20000
FLOW_WEIGHTS_DIR = './flow_weights'

OVS15_CALL = 'ovs-ofctl -O OpenFlow15'

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================


def h(host_num: int) -> str:
    """ Returns the host name for a given host number """
    return f'h{host_num}'


def s(switch_num: int) -> str:
    """ Returns the switch name for a given switch number """
    return f's{switch_num}'


def ip_to_int(ip: str) -> int:
    """ Converts a dot-format IP address to an integer. """
    i = 0
    for x in ip.split('.'):
        i = i * 256 + int(x)
    return i

# ==============================================================================
# MININET INTERFACING FUNCTIONS
# ==============================================================================


def get_ip(net: Mininet, host_num: int, switch_num: int = None):
    """
    Returns the IP address of interface connecting host to switch.
    If switch_num is not set, assumed to be the same as host_num.
    """
    host = net.get(h(host_num))
    switch = net.get(s(switch_num) if switch_num is not None else s(host_num))
    return host.connectionsTo(switch)[0][0].IP()


def get_port(net: Mininet, src: str, dest: str) -> int:
    """
    Returns the port on "src" connecting to "dst"
    """
    src, dest = net.get(src), net.get(dest)
    link = net.linksBetween(src, dest)[0]
    if src == link.intf1.node:
        return src.ports[link.intf1]
    return src.ports[link.intf2]


def add_flow(
    net: Mininet,
    switch_num: int,
    out_switch: int,
    from_host: int = None,
    from_switch: int = None,
    to_host: int = None,
    to_switch: int = None,
    filter: str = '',
) -> None:
    """
    Adds an Open vSwitch flow to a switch. Uses OpenFlow 15 protocol.

    params:
        net: Mininet newtork
        switch_num: Switch to which to add the group.
        out_switch: Output switch number
        from_host: Filter originating the traffic. Leave at None to include
                    all hosts. If set, filter argument cannot include
                    nw_src. Requires net to be given.
        from_switch:
                    Also used for originating traffic filter. Disregarded
                    if from_host is not set. If from_host is set, the
                    interface between from_host and from_switch is used
                    to filter. If not set, assumed to have the same number
                    as from_host.
        to_host: Filter receiving the traffic. Leave at None to include
                    all hosts. If set, filter argument cannot include
                    nw_dst. Requires net to be given.
        to_switch:
                    Also used for originating traffic filter. Disregarded
                    if from_host is not set. If to_host is set, the
                    interface between to_host and to_switch is used
                    to filter. If not set, assumed to have the same number
                    as to_host.
        filter: Any additional filters, given in Open vSwitch 2.15.90
                OpenFlow 15 format. If "from_host" or "to_host" is
                specified, this filter cannot include nw_src or nw_dst
                respectively.
    """
    # Build filters
    if from_host is not None:
        assert 'nw_src' not in filter, "Can't use nw_src with from_host!"
        filter = f'ip,nw_src={get_ip(net, from_host, from_switch)},' + filter
    if to_host is not None:
        assert 'nw_dst' not in filter, "Can't use nw_dst with to_host!"
        filter = f'ip,nw_dst={get_ip(net, to_host, to_switch)},' + filter
    if(filter[-1] == ','):
        filter = filter[:-1]

    port = get_port(net, s(switch_num), s(out_switch))

    # Ready to make command! Add flow:
    cmd = f'{OVS15_CALL} add-flow s{switch_num} {filter},actions=output:{port}'
    print(cmd)
    os.system(cmd)


def add_flow_to_host(
    net: Mininet,
    host_num: int,
    switch_num: int = None
) -> None:
    """
    Adds flow rules from a switch to a host using Open vSwitch OpenFlow 15.
    If switch_num is none, assumed to be the same as host_num.
    """
    if switch_num is None:
        switch_num = host_num
    port = get_port(net, s(switch_num), h(host_num))
    filter = f'ip,nw_dst={get_ip(net, host_num, switch_num)}'
    cmd = f'{OVS15_CALL} add-flow s{switch_num} {filter},actions=output:{port}'
    print(cmd)
    os.system(cmd)

# ==============================================================================
# IPERF PORT MODIFICATION
# ==============================================================================


def assert_start_ports(recv_start_port: int, send_start_port: int) -> None:
    """ Checks recv_start_port and send_start_port valid. """
    assert recv_start_port + MAX_FLOWS < send_start_port, \
        'send_start_port must be higher! Sending & recieving ports ' \
        'will overlap!'
    assert send_start_port + MAX_FLOWS * MAX_TUNNELS_PER_FLOW < 65536, \
        'send_start_port + MAX_FLOWS * MAX_TUNNELS_PER_FLOW >= 65536! ' \
        'Insufficient space for iperf sessions.'


def get_iperf_ports(
    client_num: int,
    server_num: int,
    recv_start_port: int = DEFAULT_RECV_START_PORT,
    send_start_port: int = DEFAULT_SEND_START_PORT,
) -> Tuple[int, int]:
    """
    Returns a tuple (client_port, server_port) for this iperf connection. Iperf
    client/server must be run on these ports for proper port modification.

    params:
        client_num: Client host #
        server_num: Server host #
        recv_start_port: Start port for receiver iperf sessions
        send_start_port: Start port for sender iperf sessions
    """
    assert_start_ports(recv_start_port, send_start_port)
    client_port = send_start_port + server_num
    server_port = recv_start_port + client_num
    return client_port, server_port


def start_daemon(
        net: Mininet,
        host_num: int,
        switch_num: int = None,
        recv_start_port: int = DEFAULT_RECV_START_PORT,
        send_start_port: int = DEFAULT_SEND_START_PORT,
        weight_path: str = None,
        stdout: str = '/dev/null',
        stderr: str = '/dev/null',
) -> None:
    """
    Mangles source/destination ports of UDP packets being exchanged by
    this host.
    Requires a weighted_tunnels executable in the current path.

    params:
        host_num: Host to mod ports
        net: Mininet newtork.
        switch_num:
            Switch the host is connected to. If not set, assumed to be
            the same number as the host.
        recv_start_port: Start port for receiver iperf sessions
        send_start_port: Start port for sender iperf sessions
        weight_path: Path to the weight file used by this port mod session.

    """
    assert_start_ports(recv_start_port, send_start_port)
    if weight_path is None:
        weight_path = FLOW_WEIGHTS_DIR + f'/h{host_num}.txt'
    # Modify ports
    host = net.get(h(host_num))
    ip = get_ip(net, host_num, switch_num)
    if False:
        cmd = f'valgrind --leak-check=full ' \
              f'--log-file=iperf_results/d{host_num}.val ./weighted_tunnels ' \
              f'-i {ip_to_int(ip)} ' \
              f'-w {weight_path} ' \
              f'-r {recv_start_port} ' \
              f'-s {send_start_port} ' \
              f'-q 58 ' \
              f'-v > iperf_results/d{host_num}.txt &'
    elif False:
        cmd = f'./weighted_tunnels ' \
              f'-i {ip_to_int(ip)} ' \
              f'-w {weight_path} ' \
              f'-r {recv_start_port} ' \
              f'-s {send_start_port} ' \
              f'-q 58 ' \
              f'-v > iperf_results/d{host_num}.txt &'
    else:
        cmd = f'./weighted_tunnels ' \
                f'-i {ip_to_int(ip)} ' \
                f'-w {weight_path} ' \
                f'-r {recv_start_port} ' \
                f'-s {send_start_port} ' \
                f'-q 58 ' \
                f'1> {stdout} 2> {stderr} & '

    host.cmd(cmd)
    print(cmd)

    # Use iptables to send packets to port modification
    host.cmd('iptables -F OUTPUT')
    host.cmd('iptables -A OUTPUT -p udp -j NFQUEUE --queue-num 58')
    host.cmd('iptables -F INPUT')
    host.cmd('iptables -A INPUT -p udp -j NFQUEUE --queue-num 58')


def get_iperf_commands(
    net: Mininet,
    client_num: int,
    server_num: int,
    iperf_client_args: str = '',
    iperf_server_args: str = '',
    server_switch_num: int = None,
    daemon: bool = True
) -> str:
    """
    Runs iperf between this client and server. Returns client and server
    with commands running. Use rval[0].waitOutput() for client output and
    rval[1].waitOutput() for server output.

    params:
        net: Mininet newtork.
        client_num: Client host #
        server_num: Server host #
        iperf_client_args: Arguments for the iperf client
        iperf_server_args: Arguemnts for the iperf server
        port_range_min: Minimum port used for iperf sessions.
        server_switch_num:
            Switch the server is connected to. If not set, assumed to be
            the same switch number as the client.
        daemon: True to add "&" at end of command for daemon running
    """
    clientport, serverport = get_iperf_ports(client_num, server_num)
    server_ip = get_ip(net, server_num, server_switch_num)
    client_command = f'iperf3 -c {server_ip} ' \
                     f'-p {serverport} ' \
                     f'--cport {clientport} ' \
                     f'-u -4 ' \
                     f'{iperf_client_args}'
    server_command = f'iperf3 -s -4 ' \
                     f'-p {serverport} ' \
                     f'{iperf_server_args}'
    if daemon:
        client_command += ' & '
        server_command += ' & '
    return client_command, server_command


def set_tunnel_weights(
    host_num: int,
    weights: List[List[float]],
    dummy_self_row: bool = True,
    weight_path: str = None,
) -> None:
    """
    Sets tunnel weights for a given host. Must have a port modifying client
    running on that host.
    params:
        host_num: Host for which to set tunnel weights.
        weights: List of lists of weights. The top-level list holds one sublist
                 for each host. Each sublist holds a float for each tunnel.
                      e.g. We're sending a message to host 0. Host 0 would
                           like its messages to host 1 to be sent over three
                           tunnels in ratios 5:6:7. Host 0 would like its
                           messages to host 1 to be sent over two tunnels in 
                           ratios 2:3.
                           The lists are formatted as follows:
                           [
                               [] # To host 0 from host 0! Dummy row!!
                               [5, 6, 7] # To host 1.
                               [2, 3]    # To host 2
                           ]
                           All unspecified weights are assumed to be 0.
        dummy_self_row: If set to True, will insert an extra row in the self->
                        self position.
        weight_path: Path to the weight file used by this port mod session.

    """
    if weight_path is None:
        weight_path = FLOW_WEIGHTS_DIR + f'/h{host_num}.txt'
    if len(weights) > host_num and dummy_self_row:
        weights.insert(host_num, [])
    msg = '\n'.join([','.join([str(f) for f in w]) for w in weights])
    with open(weight_path + '.tmp', 'w') as f:
        f.write(msg)
    os.rename(weight_path + '.tmp', weight_path)


def add_flow_tunnel(
    net: Mininet,
    tunnel_num: int,
    switch_num: int,
    out_switch: int,
    from_host: int,
    to_host: int,
    from_switch: int = None,
    to_switch: int = None,
    recv_start_port: int = DEFAULT_RECV_START_PORT,
    send_start_port: int = DEFAULT_SEND_START_PORT
) -> None:
    """
    Adds an Open vSwitch flow to a switch with tunnel number tunnel_num. Used
    when port modification adds multiple tunnels.

    params:
        tunnel_num: Tunnel number for this flow.
        switch_num: Switch to which to add the group.
        out_switch: Output switch number
        from_host: Filter originating the traffic. Leave at None to include
                    all hosts. If set, filter argument cannot include
                    nw_src. Requires net to be given.
        to_host: Filter receiving the traffic. Leave at None to include
                    all hosts. If set, filter argument cannot include
                    nw_dst. Requires net to be given.
        from_switch:
            Also used for originating traffic filter. Disregarded
            if from_host is not set. If from_host is set, the
            interface between from_host and from_switch is used
            to filter. If not set, assumed to have the same number
            as from_host.
        to_switch:
                    Also used for originating traffic filter. Disregarded
                    if from_host is not set. If to_host is set, the
                    interface between to_host and to_switch is used
                    to filter. If not set, assumed to have the same number
                    as to_host.
        net: Mininet newtork. Only needed for from_host or to_host
        filter: Any additional filters, given in Open vSwitch 2.15.90
                OpenFlow 15 format. If "from_host" or "to_host" is
                specified, this filter cannot include nw_src or nw_dst
                respectively.
        recv_start_port: Number tunnel to use
        recv_start_port: Start port for receiver iperf sessions
        send_start_port: Start port for sender iperf sessions
    """
    assert_start_ports(recv_start_port, send_start_port)
    sport = send_start_port + to_host * MAX_TUNNELS_PER_FLOW + tunnel_num
    for proto in ['udp']:
        filter = f'{proto},{proto}_src={sport}'
        add_flow(
            net=net,
            switch_num=switch_num,
            out_switch=out_switch,
            to_host=to_host,
            to_switch=to_switch,
            from_host=from_host,
            from_switch=from_switch,
            filter=filter
        )
