#!/usr/bin/env python3
import sys
from device_test import DeviceTest
import time

class RebootTest(DeviceTest):
   
    def get_parser(self):
        if self.argparser is None:
            argparser=super().get_parser("rolling_reboot_test.py","Tests rolling reboot")
            argparser.add_argument('--reboot-delay-seconds',
                                   type=int,
                                   default=0,
                                   help="Seconds to delay between bootup connectivity and reboot")
  
        return self.argparser


    def do_reboot_torture(self):
        args = self.get_args()
        """
        Do 200 reboots, making sure boot slot doesn't change
        """
        num_reboots=100
        print(f"Starting reboot torture tests with {num_reboots} reboots")
        boot_slot = None
        for i in range (num_reboots):
            print(f"Starting reboot {i} for {args.device}")
            self.reboot()
            print(f"reboot {i} complete for {args.device}")
            if args.reboot_delay_seconds:
               print(f"sleeping {args.reboot_delay_seconds} after connectivity re-established")
               time.sleep(args.reboot_delay_seconds)

    
    def do_test(self):
        print("Starting test")
        self.do_reboot_torture()
        return 0

if __name__ == '__main__':
    test = RebootTest()
    sys.exit(test.do_test())
