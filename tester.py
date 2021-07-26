#!/usr/bin/python3

from mininet.net import Mininet
from mininet.topo import Topo
from mininet.log import setLogLevel
from weighted_tunnels import add_flow_tunnel, get_iperf_commands, connect_host
from weighted_tunnels import weight_tunnels, set_tunnel_weights
import os
import time
import re

# Used for parsing Iperf server output in Mbps
IPERF_BW_REGEX = r'\[  \d\]\s*0.\d+\s*\-\s*([\d\.]+).*?([\d\.]+) Mbits\/sec'


class Intersection(Topo):
    """
    An "intersection" of switches. Topology consists of M hosts and N central
    switches. Each host has its own switch, and each central switch is
    connected to all host switches.
    In all, there are M + N switches and M * N links.
    """
    def __init__(self, num_hosts: int, num_central_switches: int, *args):
        self.num_hosts = num_hosts
        self.num_central_switches = num_central_switches
        self.streams = []
        super().__init__(*args)  # This calls build!

    def build(self) -> None:
        """ Builds this topology """
        # Init hosts and ingress switches
        for i in range(self.num_hosts):
            self.addLink(self.addHost(f'h{i}'), self.addSwitch(f's{i}'))
        # Init and connect central switches
        for i in range(
            self.num_hosts, self.num_hosts + self.num_central_switches
        ):
            self.addSwitch(f's{i}')
            for j in range(self.num_hosts):
                self.addLink(f's{i}', f's{j}')

    def add_flows(self, net: Mininet) -> None:
        """ Adds flows to this topology """
        # Connect hosts
        for i in range(self.num_hosts):
            connect_host(net=net, host_num=i)

        # Stress test for number of flow rules >:)
        for source in range(self.num_hosts):
            for dest in range(self.num_hosts):
                for cswitch in range(
                    self.num_hosts, self.num_hosts + self.num_central_switches
                ):
                    if source == dest:
                        continue
                    # Flow tunnel for source switch >> center
                    add_flow_tunnel(
                        net=net,
                        switch_num=source,
                        out_switch=cswitch,
                        from_host=source,
                        to_host=dest,
                        tunnel_num=cswitch - self.num_hosts
                    )
                    # Flow tunnel for center switch >> dest
                    add_flow_tunnel(
                        net=net,
                        switch_num=cswitch,
                        out_switch=dest,
                        from_host=source,
                        to_host=dest,
                        tunnel_num=cswitch - self.num_hosts
                    )

        # Add default drop rule to all but 1 central switch to avoid broadcast
        # storms.
        for cswitch in range(
            self.num_hosts + 1, self.num_hosts + self.num_central_switches
        ):
            cmd = f'ovs-ofctl -O OpenFlow15 ' \
                  f'add-flow s{cswitch} priority=0,actions=drop'
            print(cmd)
            os.system(cmd)

    def weight_tunnels(self, net: Mininet) -> None:
        """ Weights tunnels for all hosts in this topology"""
        for i in range(self.num_hosts):
            weight_tunnels(net=net, host_num=i)
            set_tunnel_weights(
                i, [[1] * self.num_central_switches] * (self.num_hosts - 1)
            )
        time.sleep(1)

    def run_iperfs(
        self, net: Mininet,
        out_dir: str,
        iperf_duration: int = 30,  # Seconds
        bw: str = '1G'
    ):
        """ Runs together iperf between all pairs in this topology """
        s_cmds = []
        c_cmds = []
        # Put together commands
        for source in range(self.num_hosts):
            for dest in range(self.num_hosts):
                if source == dest:
                    continue
                c_args = f'-t {iperf_duration} -b {bw}'
                c_args += f' -i 1 > {out_dir}/c_h{source}-h{dest}.txt 2>&1'
                s_args = f' -i 1 > {out_dir}/s_h{source}-h{dest}.txt 2>&1'
                c_cmd, s_cmd = get_iperf_commands(
                    net=net,
                    client_num=source,
                    server_num=dest,
                    iperf_server_args=s_args,
                    iperf_client_args=c_args,
                    )
                c_cmds.append((source, c_cmd))
                s_cmds.append((dest, s_cmd))

        # Execute
        for c in s_cmds:
            print(c)
            net.get(f'h{c[0]}').cmd(c[1])
        time.sleep(3)
        for c in c_cmds:
            print(c)
            net.get(f'h{c[0]}').cmd(c[1])

    def parse_output(self, out_dir: str, iperf_duration: int):
        """
        Returns average bw and successful connection count for all iperf
        servers in out_dir.
        """
        worked = 0
        sum_bw = 0
        for source in range(self.num_hosts):
            for dest in range(self.num_hosts):
                if source == dest:
                    continue
                server_file = f'{out_dir}/s_h{source}-h{dest}.txt'
                with open(server_file) as f:
                    bw = re.findall(IPERF_BW_REGEX, f.read())
                    print(f'Found: {bw}')
                    bw = [(float(b[0]), float(b[1])) for b in bw]
                    bw = bw[[b[0] for b in bw].index(max([b[0] for b in bw]))]
                    print(f'Selected: {bw}')
                    if float(bw[0]) < iperf_duration * .9:
                        continue
                    worked += 1
                    sum_bw += float(bw[1])
        return sum_bw / worked, worked


def bw_test():
    """
    Tests the bandwidth compared to stock Mininet
    """
    test_bw = [1000, 1000]  # Mbps
    file = 'bw_results.txt'
    with open(file, 'w') as f:
        f.write('\t'.join([
            '# Hosts',
            'Modded BW',
            'Modded successes',
            'Unmodded BW',
            'Unmodded successes'
        ]))

    for i in range(2, 13):
        # Add new line to results file
        with open(file, 'a') as f:
            f.write(f'\n{i}')
        # Run test for modded/unmodded
        for weight_tunnels in [1, 0]:
            # Build topo
            topo = Intersection(i, 3)
            net = Mininet(topo)
            net.start()
            topo.add_flows(net)
            if weight_tunnels:
                topo.weight_tunnels(net)
            # Run iperfs
            topo.run_iperfs(
                net,
                out_dir='./iperf_results',
                iperf_duration=30,
                bw=f'{test_bw[weight_tunnels]}M'
            )
            for j in range(40, -1, -1):
                print(j)
                time.sleep(1)
            # Kill iperfs & parse
            for j in range(i):
                net.get(f'h{j}').cmd('pkill iperf')
            net.stop()
            try:
                avg_bw, num_passed = topo.parse_output('./iperf_results', 30)
                test_bw[weight_tunnels] = round(avg_bw + .5)
            except:
                avg_bw, num_passed = -1, -1
            with open(file, 'a') as f:
                f.write(f'\t{avg_bw}\t{num_passed}\t')


def weight_test():
    """
    Function for testing proper weighting. Check the number of packets that
    go through each flow for each leg!
    """
    topo = Intersection(3, 3)
    net = Mininet(topo)
    net.start()
    topo.add_flows(net)
    topo.weight_tunnels(net)

    out = 'weight_results.txt'

    weights = (
        [[.82, .14, .22], [.65, .31, .40]],
        [[.11, .29, .35], [1.2, 955, 63]],
        [[290, 101, 875], [602, 580, 333]]
    )
    for i in range(3):
        set_tunnel_weights(i, weights[i])
    time.sleep(3)
    topo.run_iperfs(
        net, out_dir='./iperf_results', iperf_duration=60, bw='100M'
    )

    with open(out, 'w') as f:
        f.write('Weight test begin!\n')
    time.sleep(30)
    with open(out, 'a') as f:
        f.write('\n' + '=' * 100 + '\nFirst leg\n' + '=' * 100 + '\n')
        for i in range(3):
            f.write(f'Ratios from s{i} during this leg: {weights[i]}\n')
        f.write('* cumulative values will differ due to previous tests\n')
    os.system(f'ovs-ofctl dump-flows s0 | grep -v priority=65535 >> {out}')
    os.system(f'ovs-ofctl dump-flows s1 | grep -v priority=65535 >> {out}')
    os.system(f'ovs-ofctl dump-flows s2 | grep -v priority=65535 >> {out}')

    weights = (
        [[1, 0, 0], [0, 0, 1]],
        [[0, 1, 0], [0, 1, 0]],
        [[0, 0, 1], [1, 0, 0]],
    )
    for i in range(3):
        set_tunnel_weights(i, weights[i])
    time.sleep(10)
    with open(out, 'a') as f:
        f.write('\n' + '=' * 100 + '\nSecond leg\n' + '=' * 100 + '\n')
        for i in range(3):
            f.write(f'Ratios from s{i} during this leg: {weights[i]}\n')
        f.write('* cumulative values will differ due to previous tests\n')
    os.system(f'ovs-ofctl dump-flows s0 | grep -v priority=65535 >> {out}')
    os.system(f'ovs-ofctl dump-flows s1 | grep -v priority=65535 >> {out}')
    os.system(f'ovs-ofctl dump-flows s2 | grep -v priority=65535 >> {out}')

    weights = (
        [[0, 0, 1], [1, 0, 0]],
        [[1, 0, 0], [0, 0, 1]],
        [[0, 1, 0], [0, 1, 0]],
    )
    for i in range(3):
        set_tunnel_weights(i, weights[i])
    time.sleep(10)
    with open(out, 'a') as f:
        f.write('\n' + '=' * 100 + '\nThird leg\n' + '=' * 100 + '\n')
        for i in range(3):
            f.write(f'Ratios from s{i} during this leg: {weights[i]}\n')
        f.write('* cumulative values will differ due to previous tests\n')
    os.system(f'ovs-ofctl dump-flows s0 | grep -v priority=65535 >> {out}')
    os.system(f'ovs-ofctl dump-flows s1 | grep -v priority=65535 >> {out}')
    os.system(f'ovs-ofctl dump-flows s2 | grep -v priority=65535 >> {out}')
    net.stop()


if __name__ == '__main__':
    # Make needed directories
    for path in ['flow_weights', 'iperf_results']:
        if not os.path.exists(path):
            os.mkdir(path)
    # Tell mininet to print useful information
    setLogLevel('info')
    # Weight test
    os.system('mn -c')
    os.system('rm iperf_results/*.txt')
    weight_test()
    # Bandwidth test
    os.system('mn -c')
    os.system('rm iperf_results/*.txt')
    bw_test()
