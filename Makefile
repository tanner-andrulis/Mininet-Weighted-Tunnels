
install_dependencies:
	apt install python3-pip git libnfnetlink-dev libnetfilter-queue-dev
	sudo apt-get install iperf3

build:
	gcc -Wall -O3 -g -o mod_iperf_ports mod_iperf_ports.c -lnfnetlink -lnetfilter_queue -pthread

clean:
	rm flow_weights/*
	rm iperf_results/*

reset_ovs:
	export PATH=$PATH:/usr/local/share/openvswitch/scripts
	ovs-ctl stop
	ovs-ctl start
	ovs-ctl status

run_tester:
	python3 tester.py

# Fixes some issues I was having with xauthority and launching Xterms.
fix_xauth:
	sudo rm ~/.Xauthority
	sudo touch /root/.Xauthority
	sudo xauth add $(xauth -f ~mininet/.Xauthority list | tail -1)
