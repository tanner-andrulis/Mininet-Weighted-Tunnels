#!/usr/bin/python3

from mininet.net import Mininet
from mininet.topo import Topo
from mininet.log import setLogLevel
import weighted_tunnels
import os
import time

class Example(Topo):
    """
    A simple example topology.

    Set up as follows:
             s2
           /    \
    h0 - s0      s1 - h1
           \    /
             s3
    """
    def __init__(self, *args):
        super().__init__(*args)  # This calls build!

    def build(self) -> None:
        h0, h1 = self.addHost('h0'), self.addHost('h1')
        s0, s1 = self.addSwitch('s0'), self.addSwitch('s1')
        s2, s3 = self.addSwitch('s2'), self.addSwitch('s3')

        #            s2
        #          /    \
        #  h0 - s0       s1 - h1
        #          \    /
        #            s3
        self.addLink(h0, s0)
        self.addLink(s0, s2)
        self.addLink(s0, s3)
        self.addLink(s2, s1)
        self.addLink(s3, s1)
        self.addLink(s1, h1)

    def run_test(self, net: Mininet) -> None:
        """ Adds flows to this topology """
        weighted_tunnels(net=net, host_num=i)

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


def run_test():
    # Make needed directories
    if not os.path.exists('./flow_weights'):
        os.mkdir('./flow_weights')
    # Tell mininet to print useful information
    setLogLevel('info')

    # Build topology
    topo = Example()
    net = Mininet(topo)
    net.start()

    # Add flow rules to connect hosts
    weighted_tunnels.add_flow_to_host(net, 0)
    weighted_tunnels.add_flow_to_host(net, 1)

    # h0 -> h1 has two tunnels:
    # Tunnel 0: s0 -> s2 -> s1
    weighted_tunnels.add_flow_tunnel(net, switch_num=0, out_switch=2, from_host=0, to_host=1, tunnel_num=0)
    weighted_tunnels.add_flow_tunnel(net, switch_num=2, out_switch=1, from_host=0, to_host=1, tunnel_num=0)
    # Tunnel 1: s0 -> s3 -> s1
    weighted_tunnels.add_flow_tunnel(net, switch_num=0, out_switch=3, from_host=0, to_host=1, tunnel_num=1)
    weighted_tunnels.add_flow_tunnel(net, switch_num=3, out_switch=1, from_host=0, to_host=1, tunnel_num=1)

    # h1 -> h0 has one tunnel, so we can just do flows:
    # All packets travel s1 -> s2 -> s0
    weighted_tunnels.add_flow(net, switch_num=2, out_switch=0, from_host=1, to_host=0)
    weighted_tunnels.add_flow(net, switch_num=3, out_switch=0, from_host=1, to_host=0)
    weighted_tunnels.add_flow(net, switch_num=1, out_switch=2, from_host=1, to_host=0)

    # Weight tunnels
    weighted_tunnels.weight_tunnels(net, 0)
    weighted_tunnels.weight_tunnels(net, 1)
    weighted_tunnels.set_tunnel_weights(host_num=0, weights=[[.3, .7]])

    # Add default drop rule to s3 so we don't have broadcast storms
    # This is because we made a loop in the topology
    os.system('ovs-ofctl -O OpenFlow15 add-flow s3 priority=0,actions=drop')

    # Start iperfs!
    client_cmd, server_cmd = weighted_tunnels.get_iperf_commands(
        net, client_num=0, server_num=1,
        iperf_server_args='> srv_out.txt',
        iperf_client_args='-b 100M -t 10 > cli_out.txt',
    )
    print(server_cmd)
    net.get('h1').cmd(server_cmd)
    time.sleep(1)
    print(client_cmd)
    net.get('h0').cmd(client_cmd)

    time.sleep(15)
    os.system('ovs-ofctl dump-flows s0 >> example_out.txt')
    net.stop()


if __name__ == '__main__':
    run_test()
