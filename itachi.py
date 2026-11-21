
"""Itachi: experimental Naruto controller with a more conservative FFA handoff."""

import mechfight
from bob_mix import Bob


class Itachi(Bob):
    name = "Itachi"

    # Naruto switches after 2 unexplained deaths.
    # Itachi waits for 3 before switching away from Sasuke.
    FFA_SWITCH_DEATHS = 3

    def base_action(self, obs):
        alive = obs["alive"]

        # Track early deaths that were not explained by our own kills.
        if self.previous_alive is not None and obs["t"] <= 90:
            kills = sum(
                event["type"] == "kill"
                for event in obs["events"]
            )
            self.unexplained += max(
                0,
                self.previous_alive - alive - kills
            )

        self.previous_alive = alive

        # Stop the practice driver only after stronger FFA evidence.
        if self.unexplained >= self.FFA_SWITCH_DEATHS:
            self.close()

        # Use Sasuke while it is responding on time.
        if self.child.poll() is None:
            reply = self.ask(
                obs,
                0.022,
                lambda action: action.get("tick") == obs["tick"]
            )
            if reply is not None:
                return reply

        # Fall back to Sakura if Sasuke is unavailable.
        return self.sakura.act(obs)


if __name__ == "__main__":
    mechfight.run(Itachi)
