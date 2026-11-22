package mech

import (
	"math"
	"testing"
)

func TestPredictMissingTrack(t *testing.T) {
	p, v := Vec3{10, 20, 3}, Vec3{40, 0, 10}
	for _, tc := range []struct {
		age  float64
		want Vec3
	}{
		{0, p},
		{0.1, Vec3{14, 20, 3}},
		{0.5, Vec3{18, 20, 3}},
		{0.7, p},
	} {
		if got := predictTrack(p, v, tc.age); got != tc.want {
			t.Errorf("age %.1f: got %v, want %v", tc.age, got, tc.want)
		}
	}
}

func TestAcousticRiskUsesAgeBearingAndKind(t *testing.T) {
	var state tacticalState
	obs := Obs{T: 10, Sounds: []Sound{
		{Kind: "mortar", Dist: "mid", Bearing: 0, Age: 0.2},
		{Kind: "gun", Dist: "near", Bearing: math.Pi / 2, Age: 0.1},
		{Kind: "npc_gun", Dist: "near", Bearing: math.Pi},
		{Kind: "gun", Dist: "near", Age: 0.6},
	}}
	state.hear(&obs)
	for _, tc := range []struct {
		time float64
		pos  Vec3
		want float64
	}{
		{10, Vec3{80, 0, 0}, 0.495},
		{10, Vec3{0, 30, 0}, 0.342},
		{10, Vec3{30, 0, 0}, 0},
		{10, Vec3{-30, 0, 0}, 0},
		{12, Vec3{80, 0, 0}, 0},
	} {
		if got := state.acousticRisk(tc.time, tc.pos); math.Abs(got-tc.want) > 1e-8 {
			t.Errorf("at %.1f and %v: got %f, want %f", tc.time, tc.pos, got, tc.want)
		}
	}
	if !state.hasBearing(10) || state.hasBearing(11) || state.lastBearing != math.Pi/2 {
		t.Fatal("nearby sound should supply a short-lived scan bearing")
	}
}
