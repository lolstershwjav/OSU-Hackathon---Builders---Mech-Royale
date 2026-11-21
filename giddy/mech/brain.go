package mech

import (
	"encoding/json"
	"math"
	"os"
)

type Bot struct {
	world     *World
	action    Action
	tick      int
	rngSeed   int64
	upgrades  []string
}

func NewBot(seed int64) *Bot {
	return &Bot{rngSeed: seed}
}

func (b *Bot) Init(obs *Observation) {
	b.world = NewWorld(obs, obs.Mech[0].ID)
	b.tick = obs.Tick
	b.upgrades = []string{}
}

func (b *Bot) Act(obs *Observation) Action {
	b.world = NewWorld(obs, obs.Mech[0].ID)
	b.tick = obs.Tick
	b.action = Action{}

	me := b.world.Me()
	if me == nil || me.Health <= 0 {
		return b.action
	}

	enemies := b.world.Enemies()

	// Zone priority
	if !b.world.InZone(me.Position) {
		b.moveToZone(me)
	} else if len(enemies) > 0 {
		b.combat(me, enemies)
	} else {
		b.farm(me)
	}

	b.manageUpgrades(me)
	return b.action
}

func (b *Bot) moveToZone(me *MechState) {
	zoneCenter := b.world.Observation.Zone.Center
	dir := zoneCenter.Sub(me.Position).Normalize()
	b.action.Move = dir.Mul(15.0) // Max speed
	b.action.GunAngle = me.GunHeading
}

func (b *Bot) combat(me *MechState, enemies []*MechState) {
	nearest := b.world.NearestEnemy()
	if nearest == nil {
		return
	}

	// Aim at nearest
	toEnemy := nearest.Position.Sub(me.Position)
	b.action.GunAngle = toEnemy.Angle()

	// Fire if aligned
	angleDiff := AngleDiff(me.GunHeading, b.action.GunAngle)
	if math.Abs(angleDiff) < 0.3 {
		b.action.Fire = true
	}

	// Movement: strafe perpendicular to enemy
	strafeDir := Vec2{-toEnemy.Y, toEnemy.X}.Normalize()
	b.action.Move = strafeDir.Mul(10.0)

	// Boost if low health
	if me.Health < me.MaxHealth*0.3 {
		b.action.Boost = true
	}
}

func (b *Bot) farm(me *MechState) {
	// Simple patrol - move in circle
	angle := float64(b.tick) * 0.01
	b.action.Move = Vec2{math.Cos(angle), math.Sin(angle)}.Mul(8.0)
	b.action.GunAngle = angle + math.Pi/2
}

func (b *Bot) manageUpgrades(me *MechState) {
	// Basic upgrade priority
	priority := []string{"armor", "plating", "gun_damage", "gun_charge", "engine", "cooling"}
	for _, upg := range priority {
		if !contains(b.upgrades, upg) {
			b.action.Upgrade = upg
			b.upgrades = append(b.upgrades, upg)
			break
		}
	}
}

func contains(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}

func Main() {
	decoder := json.NewDecoder(os.Stdin)
	encoder := json.NewEncoder(os.Stdout)

	// Read initial observation
	var obs Observation
	if err := decoder.Decode(&obs); err != nil {
		return
	}

	bot := NewBot(12345)
	bot.Init(&obs)

	// Send initial action
	encoder.Encode(bot.action)

	// Main loop
	for {
		if err := decoder.Decode(&obs); err != nil {
			break
		}
		action := bot.Act(&obs)
		encoder.Encode(action)
	}
}