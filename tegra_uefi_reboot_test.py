#!/usr/bin/env python3
import sys
from device_test import DeviceTest

class RebootTest(DeviceTest):
    def check_var_error_flag(self):
        self.get_connection().run("efivar  -p -n 04b37fe8-f6ae-480b-bdd5-37d98c5e89aa-VarErrorFlag | grep ff")
 
    def get_parser(self):
        if self.argparser is None:
            argparser=super().get_parser("tegra_uefi_reboot_test.py","Tests rolling reboot with tegra UEFI variables (see https://forums.developer.nvidia.com/t/possible-uefi-memory-leak-and-partition-full/308540)")
  
        return self.argparser


    def do_reboot_torture(self):
        """
        Do 1000 reboots, making sure boot slot doesn't change
        """
        num_reboots=1000
        print(f"Starting reboot torture tests with {num_reboots} reboots")
        for i in range (num_reboots):
            print("Checking var error flag")
            self.check_var_error_flag()
            print(f"Starting reboot {i}")
            self.reboot()
            
    
    def do_test(self):
        print("Starting test")
        self.do_reboot_torture()
        return 0

if __name__ == '__main__':
    test = RebootTest()
    sys.exit(test.do_test())
