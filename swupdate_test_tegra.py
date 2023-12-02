from device_test import DeviceTest
import sys
import subprocess
import paramiko
from timeout_decorator import timeout

class SwupdateTestTegra(DeviceTest):
    def get_parser(self):
        if self.argparser is None:
            argparser=super().get_parser("swupdate_test_tegra.py","Tests swupdate based software updates for Tegra targets")
            argparser.add_argument('-f',
                                   '--updatefile',
                                   help='The local file to use for updates',
                                   required = True)
        return self.argparser

    def validate_slot(self):
        slot = self.get_slot()
        if slot not in [ 0, 1 ]:
            raise Exception("Invalid slot {slot}")
        r = self.get_connection().run(f"mount | grep '{self.get_rootfs_for_slot(slot)} '")

    def get_rootfs_for_slot(self, slot):
        return f"/dev/mmcblk0p{slot+1}"

    def get_slot(self):
        r = self.get_connection().run("nvbootctrl get-current-slot")
        return int(r.stdout.strip())

    @timeout(120)
    def do_file_transfer(self):
        print(f"Copying update file {self.get_args().updatefile} to target")
        self.get_connection().put(local=self.get_args().updatefile, remote="/tmp/swupdate.swu")


    def transfer_file(self):
        file_transfer_incomplete = True
        while file_transfer_incomplete:
            try:
                self.do_file_transfer()
                file_transfer_incomplete = False
            except Exception as e:
                print(f"Caught error on attempt at file transfer, retrying")


    def verify_update(self):
        start_slot = self.get_slot()
        self.validate_slot()
        print(f"Rebooting from slot {start_slot}")
        self.reboot()
        end_slot = self.get_slot()
        print(f"Reboot completed, new slot is {end_slot}")
        if end_slot == start_slot:
            raise Exception("Slot did not change after update, started and ended at {start_slot}")

        self.validate_slot()

    def do_swupdate_torture(self):
        num_updates=100
        print(f"Starting swupdate tegra torture tests with {num_updates} update passes")

        for i in range (num_updates):
            print(f"Starting update {i+1}")
            self.transfer_file()
            self.get_connection().run("bash -c 'source /usr/lib/swupdate/conf.d/* && swupdate $SWUPDATE_ARGS -i /tmp/swupdate.swu'")
            self.verify_update()


    def do_test(self):
        print("Starting test")
        self.do_swupdate_torture()
        return 0


if __name__ == '__main__':
    test = SwupdateTestTegra()
    sys.exit(test.do_test())
