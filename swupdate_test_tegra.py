from device_test import DeviceTest
import sys
import subprocess
import paramiko
from timeout_decorator import timeout
import re
import random

DEFAULT_NUM_UPDATES=100
class SwupdateTestTegra(DeviceTest):

    def get_parser(self):
        if self.argparser is None:
            argparser=super().get_parser("swupdate_test_tegra.py","Tests swupdate based software updates for Tegra targets")
            argparser.add_argument('-f',
                                   '--updatefile',
                                   help='The local file to use for updates',
                                   required = True)
            argparser.add_argument('--forcecapsule',
                                   action='store_true',
                                   help='Force capsule upgrades by creating mismatch in sw-versions')
            argparser.add_argument('--mixrandom',
                                   action='store_true',
                                   help='Mix between capsule updates, non capsule updates, and reboots without updates')
            argparser.add_argument('--num-updates',
                                   type=int,
                                   default=DEFAULT_NUM_UPDATES,
                                   help=f"The number of updates to perform (default {DEFAULT_NUM_UPDATES})")
            argparser.add_argument('--verify-poweroff',
                                   action='store_true',
                                   help=f"Verify power off during capsule update is handled/recovered properly (requires interaction)")
            argparser.add_argument('--verify-unbootable-slot-handling',
                                   action='store_true',
                                   help="Verify recovery from unbootable slot (requires interaction)")
        return self.argparser

    def validate_slot(self):
        slot = self.get_slot()
        if slot not in [ 0, 1 ]:
            raise Exception(f"Invalid slot {slot}")
        r = self.get_connection().run(f"mount | grep '{self.get_rootfs_for_slot(slot)} '")

    def get_rootfs_for_slot(self, slot):
        append= ""
        if slot == 1:
            append = "_b"
        result = self.get_connection().run(f"ls -la /dev/disk/by-partlabel/APP{append}")
        return result.stdout.strip().split("/")[-1]

    def get_slot(self):
        r = self.get_connection().run("nvbootctrl get-current-slot")
        return int(r.stdout.strip())

    def get_alt_slot(self, slot):
        return 0 if int(slot) == 1 else 1

    def get_capsule_update_status(self):
        r = self.get_connection().run("nvbootctrl dump-slots-info")
        match = re.search(r'^\s*Capsule update status:\s*(\d+)\s*', r.stdout, re.MULTILINE | re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    @timeout(120)
    def do_file_transfer(self):
        print(f"Copying update file {self.get_args().updatefile} to target")
        self.get_connection().put(local=self.get_args().updatefile, remote="/tmp/swupdate.swu")

    def transfer_file(self):
        file_transfer_incomplete = True
        while file_transfer_incomplete:
            try:
                self.do_file_transfer()
                file_transfer_incomplete=False
            except Exception as e:
                print(f"Caught error on attempt at file transfer, retrying")


    def verify_update(self, check_capsule_success=False):
        start_slot = self.get_slot()
        self.validate_slot()
        print(f"Rebooting from slot {start_slot}")
        self.reboot()
        end_slot = self.get_slot()
        print(f"Reboot completed, new slot is {end_slot}")
        if end_slot == start_slot:
            raise Exception(f"Slot did not change after update, started and ended at {start_slot}")
        if check_capsule_success:
            status = self.get_capsule_update_status()
            if status != 1:
                raise Exception(f"Invalid capsule update status {status}")
        self.validate_slot()

    def create_version_mismatch(self):
        print(f"Forcing capsule update by creating version mismatch")
        self.get_connection().run("sed -E 's/[0-9]+\.[0-9]+\.[0-9]+/0.0.0/' -i /run/swupdate/sw-versions")

    def do_swupdate_torture(self):
        num_updates = self.get_args().num_updates
        reboot_count = 0
        update_count = 0
        capsule_update_count = 0

        print(f"Starting swupdate tegra torture tests with {num_updates} update passes")

        while (update_count + capsule_update_count) < (num_updates):
            update_number = update_count + capsule_update_count + 1
            print(f"Starting update {update_number}")
            force_capsule = self.get_args().forcecapsule
            reboot_only = False
            if self.get_args().mixrandom:
                option = random.randint(1, 3)
                reboot_only = True if option == 1 else False
                force_capsule = True if option == 2 else False
            if reboot_only:
                print("Performing reboot instead of update")
                start_slot = self.get_slot()
                self.reboot()
                end_slot = self.get_slot()
                if end_slot != start_slot:
                    raise Exception(f"Slot changed from {start_slot} to {end_slot} on reboot")
                reboot_count += 1
            else:
                self.transfer_file()
                check_capsule_success = False
                if force_capsule:
                    print("Forcing capsule update")
                    self.create_version_mismatch()
                    capsule_update_count += 1
                else:
                    print("Performing normal update")
                    update_count += 1
                self.get_connection().run("swupdate -v -i /tmp/swupdate.swu")
                self.verify_update(check_capsule_success = force_capsule)
            print(f"Completed successful update {update_number}")

        print(f"Test completed successfully for {update_count} normal updates, {capsule_update_count} forced capsule updates, and {reboot_count} reboots instead of updates")

    def is_capsule_update_forced(self):
        found = False
        r = self.get_connection().run("journalctl --boot=0 -u swupdate | grep 'Forcing capsule update'", warn=True)
        return r.exited == 0

    def parse_nvbootctrl_status_split(self, output):
        """
        Parses the output of 'nvbootctrl dump-slots-info' and returns a list
        of slot statuses using string manipulation.

        Args:
            output (str): The multiline string output from the command.

        Returns:
            list: A list containing the status strings for each slot.
        """
        statuses = []
        lines = output.strip().split('\n')
        for line in lines:
            if 'slot:' in line and 'status:' in line:
                # Splits the line by commas to isolate the status section
                parts = line.split(',')
                for part in parts:
                    if 'status:' in part:
                        # Splits the status part and strips whitespace
                        status_value = part.split(':')[1].strip()
                        statuses.append(status_value)
        return statuses

    def get_slot_status(self):
        r = self.get_connection().run("nvbootctrl dump-slots-info")
        return self.parse_nvbootctrl_status_split(r.stdout)

    def switch_to_alt_slot(self):
        slot = self.get_slot()
        alt_slot = self.get_alt_slot(slot)
        self.get_connection().run(f"nvbootctrl set-active-boot-slot {alt_slot}")
        print(f"Switching active boot slot to alternate slot {alt_slot}")
        self.reboot()

    def validate_bootability(self, slot=2, is_bootable=True):
        slot_status = self.get_slot_status()
        expected = "normal" if is_bootable else "unbootable"
        if slot == 2 or slot == 0:
            if slot_status[0] != expected:
                raise Exception(f"Slot status for slot 0 is {slot_status[0]} but expected {expected}")
        if slot == 2 or slot == 1:
            if slot_status[1] != expected:
                raise Exception(f"Slot status for slot 1 is {slot_status[0]} but expected {expected}")

    def do_capsule_update_power_cycle(self):
        self.create_version_mismatch()
        self.transfer_file()
        self.get_connection().run("swupdate -v -i /tmp/swupdate.swu")
        input(f"Please power cycle during capsule update on the upcoming reboot (when Update Progress ... is shown on the serial terminal), press enter to confirm and reboot will start.")
        self.reboot()

    def do_poweroff_test(self):
        if self.is_capsule_update_forced():
            print("Capsule update is currently forced, running a swupdate step before starting to clear forced status")
            self.transfer_file()
            self.get_connection().run("swupdate -v -i /tmp/swupdate.swu")
            self.verify_update(check_capsule_success=True)
            if self.is_capsule_update_forced():
                print("Capsule update is forced on second slot, running a swupdate step before starting to clear forced status")
                self.transfer_file()
                self.get_connection().run("swupdate -v -i /tmp/swupdate.swu")
                self.verify_update(check_capsule_success=True)
                if self.is_capsule_update_forced():
                    raise Exception("Capsule update forced on slots after two swupdate capsule updates, this should not happen")
        start_slot = self.get_slot()
        print(f"Starting without forced capsule update, running from slot {start_slot}, forcing capsule update")
        self.do_capsule_update_power_cycle()
        end_slot = self.get_slot()
        if start_slot != end_slot:
            raise Exception("Slot changed after power cycle during update... did you forget to power cycle or power cycle sometime other than during capsule update?")
        self.validate_bootability()
        if not self.is_capsule_update_forced():
            raise Exception(f"Capsule update should be forced for slot {self.get_alt_slot(end_slot)} after power cycle, check marker files")
        self.transfer_file()
        self.get_connection().run("swupdate -v -i /tmp/swupdate.swu")
        self.verify_update(check_capsule_success=True)
        if self.is_capsule_update_forced():
            raise Exception(f"Capsule update should not be forced after capsule update")
        self.validate_bootability()
        print("Poweroff test complete with success!")


    def do_unbootable_slot_test(self):
        self.create_version_mismatch()
        self.do_capsule_update_power_cycle()
        slot = self.get_slot()
        self.switch_to_alt_slot()
        if slot != self.get_slot():
            raise Exception(f"System didn't say on same slot after switch_to_alt_slot and capsule update power cycle, switched to slot {self.get_slot()}")
        alt_slot = self.get_alt_slot(slot)
        self.validate_bootability(slot=alt_slot, is_bootable=False)
        self.transfer_file()
        self.get_connection().run("swupdate -v -i /tmp/swupdate.swu")
        self.verify_update(check_capsule_success=True)
        if self.is_capsule_update_forced():
            raise Exception(f"Capsule update should not be forced after capsule update with unbootable target slot")
        self.validate_bootability()
        print("Unbootable slot test complete with success!")

    def do_test(self):
        print("Starting test")
        if self.get_args().verify_poweroff:
            self.do_poweroff_test()
        elif self.get_args().verify_unbootable_slot_handling:
            self.do_unbootable_slot_test()
        else:
            self.do_swupdate_torture()
        return 0


if __name__ == '__main__':
    test = SwupdateTestTegra()
    sys.exit(test.do_test())
