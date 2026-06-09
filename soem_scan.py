#!/usr/bin/env python3
"""
EtherCAT bus scan for the motosome bench — first-contact diagnostic.

Lists every slave on the bus (name, vendor/product/revision, state) and, after
mapping, the process-image sizes. Use it when the A6-EC servo arrives to confirm
it's seen and to calibrate the PDO byte offsets in drive.CiA402Pdo.

    pip install --user pysoem
    sudo python3 soem_scan.py [ifname]          # default ifname: enp4s0

To skip sudo, grant the raw-socket capability once:
    sudo setcap cap_net_raw,cap_net_admin+eip $(readlink -f $(which python3))
"""

import sys


def main(ifname: str) -> int:
    try:
        import pysoem
    except ImportError:
        print("pysoem not installed — run: pip install --user pysoem")
        return 1

    master = pysoem.Master()
    try:
        master.open(ifname)
    except Exception as e:
        print(f"cannot open {ifname}: {e}")
        print("(raw sockets need root — run with sudo, or setcap cap_net_raw)")
        return 1

    n = master.config_init()
    if n <= 0:
        print(f"no EtherCAT slaves found on {ifname} — check cabling/power")
        master.close()
        return 1

    print(f"found {n} slave(s) on {ifname}:\n")
    for i, s in enumerate(master.slaves):
        man = getattr(s, "man", 0)
        pid = getattr(s, "id", 0)
        rev = getattr(s, "rev", 0)
        print(f"  [{i}] {s.name}")
        print(f"       vendor=0x{man:08X}  product=0x{pid:08X}  rev=0x{rev:08X}")

    try:
        master.config_map()
        master.state_check(pysoem.SAFEOP_STATE, 50000)
        print("\nprocess image (after config_map):")
        for i, s in enumerate(master.slaves):
            print(f"  [{i}] {s.name}: RxPDO(out)={len(s.output)} B  "
                  f"TxPDO(in)={len(s.input)} B")
        print("\n→ match these sizes/offsets against drive.CiA402Pdo "
              "(defaults assume 11 B / 11 B, standard CSV+CSP map).")
    except Exception as e:
        print(f"\nconfig_map failed: {e}")

    try:
        master.state = pysoem.INIT_STATE
        master.write_state()
    finally:
        master.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "enp4s0"))
