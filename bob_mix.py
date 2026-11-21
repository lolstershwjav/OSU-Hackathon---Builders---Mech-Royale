"""Bob uses Giddy in practice and Lance after early FFA evidence."""
import atexit
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
from threading import Thread
from time import monotonic

import mechfight
from lance_bot import Bot as Lance
from madara import duel_action
from navigation import Arena
from tsunade import escape_plan


class Bob:
    name = "Naruto"

    def __init__(self, hello):
        self.lance = Lance(hello)
        self.mech_config = hello["config"]["mech"]
        try:
            self.arena = Arena(hello["map"]["dir"])
        except (KeyError, OSError, ValueError):
            self.arena = None
        self.escape_goal = None
        self.escape_until = 0.0
        self.escape_retry = 0.0
        self.previous_alive = None
        self.unexplained = 0
        self.requests = Queue(maxsize=1)
        self.responses = Queue()
        binary = os.getenv("BOB_GIDDY", str(Path(__file__).with_name("giddy-bin")))
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
        alive = obs["alive"]
        if self.previous_alive is not None and obs["t"] <= 90:
            kills = sum(event["type"] == "kill" for event in obs["events"])
            self.unexplained += max(0, self.previous_alive - alive - kills)
        self.previous_alive = alive
        if self.unexplained >= 2:
            self.close()
        if self.child.poll() is None:
            reply = self.ask(obs, .022, lambda action: action.get("tick") == obs["tick"])
            if reply is not None:
                return reply
        return self.lance.act(obs)


    def act(self, obs):
        action = self.base_action(obs)
        if self.arena is None:
            return action
        now = obs["t"]
        if self.escape_goal is not None and now >= self.escape_until:
            self.escape_goal = None
            self.escape_retry = now + 2
        if now >= self.escape_retry:
            plan = escape_plan(obs, self.arena, self.mech_config, self.escape_goal)
            if plan:
                if self.escape_goal is None:
                    self.escape_until = now + 8
                self.escape_goal, escape = plan
                return {**action, **escape}
            if self.escape_goal is not None:
                self.escape_goal = None
                self.escape_retry = now + 2
        duel = duel_action(obs, self.arena, self.mech_config)
        return {"tick": obs["tick"], **duel} if duel is not None else action


if __name__ == "__main__":
    mechfight.run(Bob)
