package mech

type Vec2 struct {
	X float64
	Y float64
}

type Observation struct {
	Tick        int
	Mech        MechState
	Events      []Event
	Zone        ZoneState
	Upgrades    []Upgrade
	Shop        []ShopItem
	GameState   string
}

type MechState struct {
	ID       int
	Position Vec2
	Velocity Vec2
	Health   float64
	MaxHealth float64
	Energy   float64
	MaxEnergy float64
	Heading  float64
	GunHeading float64
	Weapon   WeaponState
	Secondary WeaponState
	Status   string
}

type WeaponState struct {
	Type        string
	Charge      float64
	MaxCharge   float64
	Cooldown    float64
	Heat        float64
	MaxHeat     float64
}

type ZoneState struct {
	Center   Vec2
	Radius   float64
	NextCenter Vec2
	NextRadius float64
	TimeRemaining float64
}

type Upgrade struct {
	ID   string
	Level int
}

type ShopItem struct {
	ID    string
	Cost  int
	Count int
}

type Event struct {
	Type      string
	Tick      int
	SourceID  int
	TargetID  int
	Position  Vec2
	Damage    float64
	Weapon    string
}

type Action struct {
	Move      Vec2
	GunAngle  float64
	Fire      bool
	Boost     bool
	Buy       string
	Upgrade   string
	Message   string
}