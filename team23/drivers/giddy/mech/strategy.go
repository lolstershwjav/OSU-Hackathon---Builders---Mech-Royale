package mech

import "math"

type heardFire struct {
	origin      Vec3
	bearing, at float64
	mid, mortar bool
}

type tacticalState struct {
	gunfire                [12]heardFire
	soundCount, nextSound  int
	lastBearing, bearingAt float64
	hasSound               bool
	roof                   Vec3
	roofUntil, roofScanAt  float64
}

func (s *tacticalState) hear(o *Obs) {
	for _, cue := range o.Sounds {
		if cue.Age < 0 || cue.Age > 0.5 || (cue.Kind != "gun" && cue.Kind != "mortar") ||
			(cue.Dist != "near" && cue.Dist != "mid") {
			continue
		}
		at := o.T - cue.Age
		s.gunfire[s.nextSound] = heardFire{o.Self.Pos, cue.Bearing, at, cue.Dist == "mid", cue.Kind == "mortar"}
		s.nextSound = (s.nextSound + 1) % len(s.gunfire)
		s.soundCount = min(s.soundCount+1, len(s.gunfire))
		if cue.Dist == "near" && (!s.hasSound || at >= s.bearingAt) {
			s.lastBearing, s.bearingAt, s.hasSound = cue.Bearing, at, true
		}
	}
}

func (s *tacticalState) hasBearing(now float64) bool {
	return s.hasSound && now-s.bearingAt <= 0.6
}

// Sound reports give sectors and approximate distance bands. Do not assign an enemy ID or exact point.
func (s *tacticalState) acousticRisk(now float64, pos Vec3) float64 {
	risk := 0.0
	for _, cue := range s.gunfire[:s.soundCount] {
		age := now - cue.at
		if age < 0 || age >= 2 {
			continue
		}
		dx, dy := pos[0]-cue.origin[0], pos[1]-cue.origin[1]
		dist := hyp(dx, dy)
		if dist < 12 || (cue.mid && (dist < 45 || dist > 185)) || (!cue.mid && dist > 80) {
			continue
		}
		if math.Abs(angDiff(math.Atan2(dy, dx), cue.bearing)) <= 0.40 {
			weight := 0.36
			if cue.mortar {
				weight = 0.55
			}
			risk = math.Max(risk, weight*(1-age/2))
		}
	}
	return risk
}

// Project only when the server stops reporting the track. Visible and server-remembered positions are current.
func predictTrack(pos, vel Vec3, age float64) Vec3 {
	if age <= 0 || age > 0.6 {
		return pos
	}
	step := math.Min(age, 8/math.Max(1, hyp(vel[0], vel[1])))
	return Vec3{pos[0] + vel[0]*step, pos[1] + vel[1]*step, pos[2]}
}

func (b *Brain) trackedEye(t *Track) Vec3 {
	eye := b.eye(t)
	next := predictTrack(eye, t.Vel, b.now-t.Seen)
	if next != eye && b.W.Clear(eye, next) {
		return next
	}
	return eye
}

func (b *Brain) opportunity(me *Self, foe *Track, alive int) float64 {
	var bonus float64
	_, laser := hasWeapon(me.Weapons, "laser")
	if laser != nil && holds(foe.Weapons, "mortar") {
		bonus += 18
	}
	// A damaged rival looking away is an opportunity, independent of who it is aiming at.
	if foe.HP < 0.55*foe.MaxHP && b.showsBack(foe, me.Pos[0], me.Pos[1]) {
		bonus += 14
	}
	if alive <= 3 && foe.Dist < 100 {
		bonus += 8
	}
	return bonus
}

func (b *Brain) exposureAt(o *Obs, pos Vec3, radius float64, ignore *Track) int {
	exposed := 0
	point := Vec3{pos[0], pos[1], pos[2] + o.Self.Stats.Height*0.65}
	for _, t := range b.tracks {
		if t == ignore || b.now-t.Seen > 1.2 || hyp(t.Pos[0]-pos[0], t.Pos[1]-pos[1]) > radius {
			continue
		}
		if b.W.Clear(b.trackedEye(t), point) {
			exposed++
			if exposed >= 3 {
				break
			}
		}
	}
	return exposed
}

func (b *Brain) lootSafety(o *Obs, pos Vec3, distance, hpFrac float64) float64 {
	if distance <= 12 {
		return 1
	}
	safety := 1 - b.tactics.acousticRisk(b.now, pos)
	if hpFrac < 0.75 || distance > 30 {
		me := &o.Self
		mid := Vec3{(me.Pos[0] + pos[0]) * 0.5, (me.Pos[1] + pos[1]) * 0.5, 0}
		mid[2] = b.W.WalkAt(mid[0], mid[1])
		exposure := max(b.exposureAt(o, pos, 95, nil), b.exposureAt(o, mid, 95, nil))
		safety /= 1 + 0.6*float64(exposure)
	}
	return safety
}

func (b *Brain) safeFinish(o *Obs, foe *Track, seen []*Track) bool {
	if foe.Dist <= 25 {
		return true
	}
	x, y := o.Self.Pos[0], o.Self.Pos[1]
	gx, gy := x+(foe.Pos[0]-x)*0.55, y+(foe.Pos[1]-y)*0.55
	if !b.W.LineWalkable(x, y, gx, gy) ||
		b.now+foe.Dist/o.Self.Stats.WalkSpeed+5 > b.zoneDeadline(&o.Zone, gx, gy, 8) {
		return false
	}
	spot := Vec3{gx, gy, b.W.WalkAt(gx, gy)}
	return (len(seen) <= 1 || b.exposureAt(o, spot, 145, foe) == 0) &&
		b.tactics.acousticRisk(b.now, spot) < 0.35
}

func (b *Brain) safeDrop(o *Obs, drop *Drop, hpFrac float64, danger bool) bool {
	me := &o.Self
	d := hyp(drop.Pos[0]-me.Pos[0], drop.Pos[1]-me.Pos[1])
	if !me.InZone || danger || d > 42 || (drop.Holder != nil && *drop.Holder != me.ID) ||
		(hpFrac >= 0.90 && me.Cubes >= b.cubeCap() && me.Energy >= 70) ||
		!b.W.LineWalkable(me.Pos[0], me.Pos[1], drop.Pos[0], drop.Pos[1]) {
		return false
	}
	travel := d / me.Stats.WalkSpeed
	end := math.Max(travel, drop.LandsIn) + math.Max(0, drop.CaptureS-drop.Progress)
	return drop.LandsIn <= travel+2 &&
		b.now+end+6 < b.zoneDeadline(&o.Zone, drop.Pos[0], drop.Pos[1], 10) &&
		b.tactics.acousticRisk(b.now, drop.Pos) < 0.22 &&
		b.exposureAt(o, drop.Pos, 125, nil) == 0
}

func (b *Brain) coveredRotation(o *Obs, seen []*Track, gx, gy, slack float64) (float64, float64) {
	me := &o.Self
	x, y := me.Pos[0], me.Pos[1]
	dist := hyp(gx-x, gy-y)
	if dist < 24 || slack < 7 || len(seen) == 0 || !b.W.LineWalkable(x, y, gx, gy) {
		return b.travel(me, gx, gy)
	}
	dx, dy := (gx-x)/dist, (gy-y)/dist
	fx, fy := x+dx*16, y+dy*16
	risk := func(px, py float64) float64 {
		p := Vec3{px, py, b.W.WalkAt(px, py)}
		return float64(b.exposureAt(o, p, 150, nil)) + b.tactics.acousticRisk(b.now, p)
	}
	best := risk(fx, fy)
	bx, by := gx, gy
	for _, side := range [...]float64{-8, 8} {
		px, py := fx-dy*side, fy+dx*side
		if !b.W.LineWalkable(x, y, px, py) || !b.W.LineWalkable(px, py, gx, gy) ||
			b.zonePenalty(&o.Zone, px, py) > b.zonePenalty(&o.Zone, fx, fy) {
			continue
		}
		if score := risk(px, py); score+0.7 < best {
			bx, by, best = px, py, score
		}
	}
	return b.travel(me, bx, by)
}

func (b *Brain) takeRoof(o *Obs, mx, my *float64, boost, jump *bool) bool {
	me := &o.Self
	x, y, height := me.Pos[0], me.Pos[1], me.Pos[2]
	if b.now >= b.tactics.roofUntil {
		if b.now < b.tactics.roofScanAt || !me.Grounded || me.Energy < 75 {
			return false
		}
		b.tactics.roofScanAt = b.now + 3
		for i := 0; i < 12; i++ {
			a := float64(i) * math.Pi / 6
			px, py := x+math.Cos(a)*18, y+math.Sin(a)*18
			top := b.W.TopAt(px, py)
			if top-height < 4 || top-height > 15 || top-b.W.GroundAt(px, py) < 4 ||
				hyp(px-o.Zone.NextCenter[0], py-o.Zone.NextCenter[1]) > o.Zone.NextRadius-15 ||
				b.zoneDeadline(&o.Zone, px, py, 10) < b.now+15 {
				continue
			}
			if b.W.TopAt(px+4, py) < top-0.5 || b.W.TopAt(px-4, py) < top-0.5 ||
				b.W.TopAt(px, py+4) < top-0.5 || b.W.TopAt(px, py-4) < top-0.5 ||
				!b.W.Clear(Vec3{x, y, height + me.Stats.Height}, Vec3{x, y, top + me.Stats.Height + 3}) ||
				!b.W.Clear(Vec3{x, y, top + 3}, Vec3{px, py, top + 3}) {
				continue
			}
			b.tactics.roof, b.tactics.roofUntil, b.tactics.roofScanAt = Vec3{px, py, top}, b.now+5, b.now+25
			break
		}
	}
	if b.now >= b.tactics.roofUntil || me.Energy < 40 {
		return false
	}
	dx, dy := b.tactics.roof[0]-x, b.tactics.roof[1]-y
	d := hyp(dx, dy)
	if d < 5 && height >= b.tactics.roof[2]-0.5 {
		return true
	}
	if height < b.tactics.roof[2]+2.5 {
		*jump, *boost = me.Grounded, true
		return true
	}
	*mx, *my = dx/d, dy/d
	*boost = d > 5 && height < b.tactics.roof[2]+5
	return true
}
