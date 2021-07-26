
install_dependencies:
	apt install python3-pip git libnfnetlink-dev libnetfilter-queue-dev
	sudo apt-get install iperf3

build:
	gcc -Wall -O3 -g -o weighted_tunnels weighted_tunnels.c -lnfnetlink -lnetfilter_queue -pthread

clean:
	rm flow_weights/*
	rm iperf_results/*

reset_ovs:
	/usr/local/share/openvswitch/scripts/ovs-ctl stop
	/usr/local/share/openvswitch/scripts/ovs-ctl start
	/usr/local/share/openvswitch/scripts/ovs-ctl status

run_example:
	mn -c
	python3 example.py

run_tester:
	mn -c
	python3 tester.py

# Fixes some issues I was having with xauthority and launching Xterms.
fix_xauth:
	sudo rm ~/.Xauthority
	sudo touch /root/.Xauthority
	sudo xauth add $(xauth -f ~mininet/.Xauthority list | tail -1)
