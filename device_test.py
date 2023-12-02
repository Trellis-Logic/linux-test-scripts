from fabric import Connection
import argparse
import time
import subprocess
import platform
from timeout_decorator import timeout

class DeviceTest:
    DEFAULT_USER = 'root'
    args = None
    connection = None
    argparser = None

    def get_parser(self,parent_test="",parent_description=""):
        if self.argparser is None:
            '''
            Argument parsing for new deployment and provisioning process
            '''
            argparser = argparse.ArgumentParser(prog=parent_test,
                                                usage='%(prog)s [options]',
                                                description=parent_description,
                                                formatter_class=argparse.RawTextHelpFormatter)
            argparser.add_argument('-d',
                                   '--device',
                                   help='The IP address or name of the device')

            argparser.add_argument('-u',
                                   '--user',
                                   help=f"The SSH username (default is {self.DEFAULT_USER})")

            argparser.add_argument('-p',
                                   '--password',
                                   help='The SSH password (default is no password)')

            argparser.add_argument('-k',
                                   '--key',
                                   help='The SSH key file (used instead of password if specified)')
            argparser.add_argument('-s',
                                   '--sudo',
                                   help='Use sudo for reboot command, (must be setup NOPASSWD in visudo)',
                                   action='store_true')


            self.argparser = argparser
        return self.argparser


    def get_args(self):
        if self.args is None:
            self.args = self.get_parser().parse_args()

            if self.args.user is None:
                print(f"No user specified, using {self.DEFAULT_USER}")
                self.args.user=self.DEFAULT_USER
        return self.args

    def get_connection(self):
        args = self.get_args()
        if args.device is None:
            print("Missing device argument")
            self.get_parser().print_help()
            raise RuntimeError("Must specify device as argument")
        if self.connection is None:
            if args.key is not None:
                self.connection = Connection(
                    host=f'{args.user}@{args.device}',
                    connect_kwargs={
                        "key_filename": args.key,
                        "password": args.password
                    })
            elif args.password is not None:
                self.connection = Connection(
                    host=f'{args.user}@{args.device}',
                    connect_kwargs={
                        "password": args.password
                    })
            else:
                self.connection = Connection(
                    host=f'{args.user}@{args.device}',
                    connect_kwargs={
                        "password": "",
                        "look_for_keys": False
                    })
        return self.connection

    @timeout(5)
    def verify_connection(self):
        self.connection = None
        self.get_connection().run("echo connection test", timeout=3)

    def wait_for_device(self):
        conn = self.get_connection()
        print(f'Trying to connect to {self.get_args().device}....')
        success = False
        ip = None
        quiet = True
        while not success:
            try:
                self.verify_connection()
                time.sleep(3)
                self.verify_connection()
                time.sleep(15)
                self.verify_connection()
                success = True
            except Exception as e:
                if not quiet:
                    print('Exception {e} connecting, retrying..')
                    quiet = True
                time.sleep(3)


    def ping(self):
        args=self.get_args()
        """
        Returns True if host (str) responds to a ping request.
        Remember that a host may not respond to a ping (ICMP) request even if the host name is valid.
        See https://stackoverflow.com/a/32684938/1446624
        """

        # Option for the number of packets as a function of
        param = '-n' if platform.system().lower() == 'windows' else '-c'

        # Building the command. Ex: "ping -c 1 google.com"
        command = ['ping', param, '1', args.device]

        return subprocess.call(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT) == 0

    def wait_for_device_removal(self):
        while self.ping():
            pass

    def reboot(self):
        args=self.get_args()
        conn = self.get_connection()
        print("Rebooting device")
        if args.sudo:
            result = conn.sudo("reboot", warn=True, shell=False)
        else:
            result = conn.run("reboot", warn=True)
        self.wait_for_device_removal()
        self.wait_for_device()


    def get_machine_id(self):
        conn = self.get_connection()
        result = conn.run("systemd-machine-id-setup --print", hide=True, timeout=3.0)
        return result.stdout
