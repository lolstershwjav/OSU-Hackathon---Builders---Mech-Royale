"""Naruto delegates to Sasuke with a Sakura fallback and a Madara duel override."""
import atexit
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
from threading import Thread
from time import monotonic

import mechfight
from sakura import Bot as Sakura
from madara import duel_action
from navigation import Arena


class Bob:
    name = "Naruto"

    def __init__(self, hello):
        self.sakura = Sakura(hello)
        self.mech_config = hello["config"]["mech"]
        try:
            self.arena = Arena(hello["map"]["dir"])
        except (KeyError, OSError, ValueError):
            self.arena = None
        self.requests = Queue(maxsize=1)
        self.responses = Queue()
        binary = os.getenv("NARUTO_SASUKE", str(Path(__file__).with_name("sasuke-bin")))
        self.child = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        atexit.register(self.close)
        Thread(target=self.relay, daemon=True).start()
        if self.ask(hello, 8, lambda reply: reply.get("type") == "ready") is None:
            self.close()

    def close(self):
        if self.child.poll() is None:
            self.child.kill()
            self.child.wait()

    def relay(self):
        try:
            while self.child.poll() is None:
                self.child.stdin.write(json.dumps(self.requests.get()) + "\n")
                self.child.stdin.flush()
                line = self.child.stdout.readline()
                if not line:
                    break
                self.responses.put(json.loads(line))
        except (OSError, ValueError):
            pass
        finally:
            self.responses.put(None)

    def ask(self, message, timeout, accepts):
        try:
            self.requests.put_nowait(message)
            deadline = monotonic() + timeout
            while monotonic() < deadline:
                reply = self.responses.get(timeout=max(0, deadline - monotonic()))
                if reply is None:
                    return None
                if accepts(reply):
                    return reply
        except (Empty, Full):
            pass
        return None

    def base_action(self, obs):
        if self.child.poll() is None:
            reply = self.ask(obs, .022, lambda action: action.get("tick") == obs["tick"])
            if reply is not None:
                return reply
        return self.sakura.act(obs)

    def act(self, obs):
        action = self.base_action(obs)
        if self.arena is None:
            return action
        duel = duel_action(obs, self.arena, self.mech_config)
        return {"tick": obs["tick"], **duel} if duel is not None else action


if __name__ == "__main__":
    mechfight.run(Bob)
