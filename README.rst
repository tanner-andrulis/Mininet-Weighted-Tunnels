Weighted tunnels for Mininet / Open vSwitch
========================================================
Full documentation coming soon!

Files:

  mod_iperf_ports.c: Port modification daemon

  mod_ports.py: Helpful Python commands to operate the daemon

  tester.py: Testing script


Building
--------

::

    sudo -s
    make install_dependencies
    make build

Running
-------

::

    make reset_ovs # Only needed on system startup!
    make run_tester
