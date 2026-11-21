package mech

import (
	"math"
)

func (v Vec2) Add(other Vec2) Vec2 {
	return Vec2{v.X + other.X, v.Y + other.Y}
}

func (v Vec2) Sub(other Vec2) Vec2 {
	return Vec2{v.X - other.X, v.Y - other.Y}
}

func (v Vec2) Mul(scalar float64) Vec2 {
	return Vec2{v.X * scalar, v.Y * scalar}
}

func (v Vec2) Length() float64 {
	return math.Hypot(v.X, v.Y)
}

func (v Vec2) Normalize() Vec2 {
	l := v.Length()
	if l == 0 {
		return Vec2{0, 0}
	}
	return Vec2{v.X / l, v.Y / l}
}

func (v Vec2) Dist(other Vec2) float64 {
	return v.Sub(other).Length()
}

func (v Vec2) Angle() float64 {
	return math.Atan2(v.Y, v.X)
}

func AngleDiff(a, b float64) float64 {
	diff := a - b
	for diff > math.Pi {
		diff -= 2 * math.Pi
	}
	for diff < -math.Pi {
		diff += 2 * math.Pi
	}
	return diff
}

type World struct {
	Observation *Observation
	MyID        int
}

func NewWorld(obs *Observation, myID int) *World {
	return &World{Observation: obs, MyID: myID}
}

func (w *World) Me() *MechState {
	for i := range w.Observation.Mech {
		if w.Observation.Mech[i].ID == w.MyID {
			return &w.Observation.Mech[i]
		}
	}
	return nil
}

func (w *World) Enemies() []*MechState {
	me := w.Me()
	if me == nil {
		return nil
	}
	var enemies []*MechState
	for i := range w.Observation.Mech {
		if w.Observation.Mech[i].ID != w.MyID && w.Observation.Mech[i].Health > 0 {
			enemies = append(enemies, &w.Observation.Mech[i])
		}
	}
	return enemies
}

func (w *World) AliveEnemies() int {
	return len(w.Enemies())
}

func (w *World) NearestEnemy() *MechState {
	me := w.Me()
	if me == nil {
		return nil
	}
	var nearest *MechState
	minDist := math.MaxFloat64
	for _, e := range w.Enemies() {
		d := me.Position.Dist(e.Position)
		if d < minDist {
			minDist = d
			nearest = e
		}
	}
	return nearest
}

func (w *World) InZone(pos Vec2) bool {
	return pos.Dist(w.Observation.Zone.Center) <= w.Observation.Zone.Radius
}

func (w *World) TimeToZone(pos Vec2, speed float64) float64 {
	dist := pos.Dist(w.Observation.Zone.Center) - w.Observation.Zone.Radius
	if dist <= 0 {
		return 0
	}
	return dist / speed
}