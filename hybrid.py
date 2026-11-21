"""Run Lance in practice and switch to our policy on early FFA evidence."""
import mechfight
from bot import Bot
from lance_bot import Bot as Lance


class Hybrid:
    def __init__(self, hello):
        self.practice = Lance(hello)
        self.ffa = Bot(hello)
        self.previous_alive = None
        self.unexplained = 0
        self.switched = False

    def act(self, obs):
        alive = obs["alive"]
        if self.previous_alive is not None and obs["t"] <= 90:
            kills = sum(event["type"] == "kill" for event in obs["events"])
            self.unexplained += max(0, self.previous_alive - alive - kills)
            self.switched = self.unexplained >= 2
        self.previous_alive = alive
        if self.switched:
            return self.ffa.act(obs)
        return self.practice.act(obs)


if __name__ == "__main__":
    mechfight.run(Hybrid)
