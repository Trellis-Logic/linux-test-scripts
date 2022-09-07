#!/usr/bin/env python3
import fabric2
from fabric2 import Connection
import argparse
import time
import traceback
import sys
import re
import subprocess
import platform
import textwrap
from device_test import DeviceTest

class RebootTest(DeviceTest):
   
    def get_parser(self):
        if self.argparser is None:
            argparser=super().get_parser("rolling_reboot_test.py","Tests rolling reboot")
  
        return self.argparser


    def do_reboot_torture(self):
        """
        Do 200 reboots, making sure boot slot doesn't change
        """
        num_reboots=100
        print(f"Starting reboot torture tests with {num_reboots} reboots")
        boot_slot = None
        for i in range (num_reboots):
            print(f"Starting reboot {i}")
            self.reboot()
    
    def do_test(self):
        print("Starting test")
        self.do_reboot_torture()
        return 0

if __name__ == '__main__':
    test = RebootTest()
    sys.exit(test.do_test())
