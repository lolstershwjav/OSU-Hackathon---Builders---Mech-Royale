"""A placement-first, lightweight bot for Builders Mech Royale.

Deterministic policies, dynamic loot routing, trajectory aiming, threat assessment,
and obstacle avoidance. Standard library only, comfortably below the 50ms budget.
"""
import json
import math
import os
import random
import mechfight as mf

TAU = math.tau

def norm(x, y):
    r = math.hypot(x, y)
    return (x / r, y / r) if r > 1e-7 else (0., 0.)

def dist(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])

def clamp(x, a, b):
    return min(b, max(a, x))

def angle(a):
    return math.atan2(a[1], a[0])

def wrap(x):
    return (x + math.pi) % TAU - math.pi

class Bot:
    name = 'Oval Survivor v1'

    def __init__(self, hello):
        self.cfg = hello['config']
        self.rng = random.Random(hello.get('seed', 0))
        self.pick_radius = self.cfg['loot']['pickup_radius']
        self.last_pos = None
        self.stuck = 0
        self.escape_until = 0
        self.escape_angle = 0.
        self.goal_id = None
        self.goal_type = None
        self.shot_laser = False
        self.prisms = []
        try:
            path = os.path.join(hello['map']['dir'], 'v2/world.json')
            world = json.load(open(path, encoding='utf-8'))
            for p in world.get('prisms', []):
                r = p.get('ring', [])
                if r:
                    xs = r[::2]; ys = r[1::2]
                    self.prisms.append((min(xs),max(xs),min(ys),max(ys),p['base'],p['top']))
        except (OSError, KeyError, ValueError):
            pass

    def obstacle_vector(self, x, y, z, dx, dy):
        # Near-field AABB repulsion; only solids high enough to block walking.
        ox = oy = 0.
        for x0,x1,y0,y1,base,top in self.prisms:
            if top < z+2 or base > z+8:
                continue
            if x < x0-14 or x > x1+14 or y < y0-14 or y > y1+14:
                continue
            px = clamp(x, x0, x1)
            py = clamp(y, y0, y1)
            ux,uy=x-px,y-py
            dd=math.hypot(ux,uy)
            if dd < 10 and dd > 0.01:
                strength=(10-dd)/10
                ox+=strength*ux/dd*2.5
                oy+=strength*uy/dd*2.5
            # If aiming at a building's face, bias toward the closer end of it.
            if dd < 9 and dd > 0.01 and dx*ux+dy*uy < -0.1:
                ox += -uy/dd*0.55
                oy += ux/dd*0.55
        return ox,oy

    def act(self, obs):
        me=obs['self']; pos=me['pos']; x,y,z=pos
        t=obs['t']; hp=me['hp']; max_hp=me['max_hp']; energy=me['energy']
        zone=obs['zone']; cz=zone['next_center']; radius=zone['next_radius']
        my_center=(x,y)
        enemies=[p for p in obs['mechs'] if p['kind']=='mech']
        enemies.sort(key=lambda e: dist(e['pos'], pos))
        closest=enemies[0] if enemies else None
        ed=dist(closest['pos'],pos) if closest else 9999
        danger=ed < 95
        damaged=any(e.get('type')=='damaged' and e.get('weapon')!='zone' for e in obs.get('events',[]))
        if damaged: self.escape_until=t+7
        recovering=t < self.escape_until or hp/max_hp < 0.38
        # Pick upgrades by survival value; scale weapon preference when healthy.
        act={'tick':obs['tick'], 'fire':[False,False]}
        if obs.get('offer'):
            choices=obs['offer']['choices']
            def upgrade_score(c):
                k=c['id']
                rank={'armor':100,'regen':98,'max_hp':95,'hp':94,'speed':90,
                      'energy':85,'boost':80,'mobility':80,'gun_rate':67,
                      'gun_damage':66,'lightning_range':62,'laser_damage':64,
                      'sensors':32}
                return max([v for key,v in rank.items() if key in k] or [55])
            act['pick']=max(range(len(choices)),key=lambda i:upgrade_score(choices[i]))
        # Take a second weapon, preferring laser long-range or lightning for defense.
        held=[w['kind'] for w in me['weapons'] if w]
        def weapon_value(k): return {'laser':95,'lightning':87,'mortar':70,'gun':0}.get(k,0)
        possible=sorted((p for p in obs['pickups'] if dist(p['pos'],pos)<self.pick_radius
                         and abs(p['pos'][2]-z)<4 and p['kind'] not in held),
                        key=lambda p:weapon_value(p['kind']),reverse=True)
        if possible:
            best=possible[0]
            empty=any(w is None for w in me['weapons'])
            if empty:
                act['take']=best['id']
            elif weapon_value(best['kind'])>weapon_value(held[-1])+18:
                act['take']=best['id'];act['drop']=1
        # Prevent zone entrapment: aim for a point well inside the next circle.
        next_d=dist(pos,cz)
        safe_radius=max(12.,radius-35)
        urgency=max(0., next_d-safe_radius)
        inside=next_d<safe_radius
        goal=list(cz)
        strategic_goal=None
        # Zone interior point biased toward our current position, avoid a needless center rush.
        if inside:
            goal=[x,y]
        else:
            ux,uy=norm(x-cz[0],y-cz[1]);goal=[cz[0]+ux*max(0,radius-55),cz[1]+uy*max(0,radius-55)]
        # When zone is safe, collect only close useful loot; avoid enemies on the way.
        if inside and not recovering:
            opportunities=[]
            for p in obs['cubes']:
                d=dist(p['pos'],pos)
                if d<100 and dist(p['pos'],cz)<radius-10:
                    opportunities.append((d*0.65,p['pos'],'cube'))
            for p in obs['pickups']:
                if p['kind'] in held or p['kind']=='gun':continue
                d=dist(p['pos'],pos)
                if d<115 and dist(p['pos'],cz)<radius-10:
                    opportunities.append((d*(0.47 if not me['weapons'][1] else 1.1),p['pos'],'weapon'))
            for p in obs['drops']:
                d=dist(p['pos'],pos)
                if (p['lands_in']<max(18.,d/14) and d<155 and
                   not any(dist(e['pos'],p['pos'])<55 for e in enemies)):
                    opportunities.append((d*.55-35,p['pos'],'drop'))
            if opportunities:
                opportunities.sort(key=lambda a:a[0])
                weight,loc,kind=opportunities[0]
                if weight<85 and not any(dist(e['pos'],loc)<42 for e in enemies):
                    goal=loc[:2];strategic_goal=kind
        # Flee toward clear space when a hostile is nearby, rather than duel automatically.
        if closest and (recovering or ed<36 or (hp<closest.get('hp',max_hp)*0.7 and ed<105)):
            awayx,awayy=norm(x-closest['pos'][0],y-closest['pos'][1])
            # Avoid fleeing through the zone wall.
            tocx,tocy=norm(cz[0]-x,cz[1]-y)
            zone_pressure=clamp((next_d-radius+100)/100,0,2)
            gx=awayx*1.8+tocx*zone_pressure
            gy=awayy*1.8+tocy*zone_pressure
            gx,gy=norm(gx,gy)
            goal=[x+gx*80,y+gy*80]
            strategic_goal='escape'
        vx,vy=norm(goal[0]-x,goal[1]-y)
        # Separation and projectile displacement.
        ax=ay=0.
        for e in enemies[:5]:
            dd=dist(e['pos'],pos)
            if dd<90 and dd>0.1:
                q=clamp((90-dd)/90,0,1)
                ax+=(x-e['pos'][0])/dd * q*(2.5 if recovering else 1.35)
                ay+=(y-e['pos'][1])/dd * q*(2.5 if recovering else 1.35)
        for shot in obs.get('projectiles',[])[:40]:
            sx,sy=shot['pos'][:2];svx,svy=shot['vel'][:2]
            speed2=svx*svx+svy*svy
            if speed2<5:continue
            dt=clamp(((x-sx)*svx+(y-sy)*svy)/speed2,0,.7)
            px,py=sx+svx*dt,sy+svy*dt
            d=math.hypot(x-px,y-py)
            if d<13 and dt>.02:
                nx,ny=norm(-svy,svx)
                ax+=nx*(13-d)/13*1.7
                ay+=ny*(13-d)/13*1.7
        ox,oy=self.obstacle_vector(x,y,z,vx+ax,vy+ay)
        mx,my=norm(vx+ax+ox,vy+ay+oy)
        if self.last_pos is not None and math.hypot(x-self.last_pos[0],y-self.last_pos[1])<0.14 and math.hypot(mx,my)>0.5:
            self.stuck+=1
        else:
            self.stuck=max(0,self.stuck-2)
        self.last_pos=(x,y)
        if self.stuck>14 and t>self.escape_until-5:
            self.escape_angle=angle((mx,my))+self.rng.choice([-1,1])*1.6
            self.escape_until=max(self.escape_until,t+2)
            self.stuck=0
        if t<self.escape_until and self.stuck>3:
            mx,my=math.cos(self.escape_angle),math.sin(self.escape_angle)
        # Stay still while well inside safe circle with nothing worth collecting:
        # this conserves energy and permits HP regeneration.
        idle=inside and strategic_goal is None and not danger
        if idle: mx=my=0.
        act['move']=[mx,my]
        act['boost']= bool((urgency>45 or (danger and recovering and ed<80)) and energy>20 and math.hypot(mx,my)>.4)
        act['jump']=bool(self.stuck>9 and me['grounded'] and energy>28)
        # Fire only on favorable visible targets; always face closest threat for armor.
        target=None
        if closest and ed<225:
            target=closest
        elif not recovering and hp/max_hp>.7:
            drones=[p for p in obs['mechs'] if p['kind']=='npc' and dist(p['pos'],pos)<70]
            if drones and not danger: target=min(drones,key=lambda p:dist(p['pos'],pos))
        aimyaw=angle((cz[0]-x,cz[1]-y)) if not target else angle((target['pos'][0]-x,target['pos'][1]-y))
        aimpitch=0.
        if target:
            dx=target['pos'][0]-x;dy=target['pos'][1]-y;dd=math.hypot(dx,dy)
            gun=me['weapons'][0]
            v=gun['stats'].get('speed',252) if gun else 252
            lead=clamp(dd/max(1,v),0,.85)
            tx=target['pos'][0]+target['vel'][0]*lead
            ty=target['pos'][1]+target['vel'][1]*lead
            tz=target['pos'][2]+(4.5 if target['kind']=='mech' else 1.8)+target['vel'][2]*lead
            aimyaw=math.atan2(ty-y,tx-x)
            aimpitch=math.atan2(tz-(z+me['stats'].get('muzzle_height',7)),max(1,math.hypot(tx-x,ty-y)))
        act['aim']=[aimyaw,clamp(aimpitch,-1.3,1.3)]
        # Allow retaliation, but avoid needless aggro and reset regen timer.
        shoot=bool(target and (target['kind']=='mech' and ed<125 and (danger or damaged)) or
                   target and target['kind']=='npc' and not recovering)
        if target and shoot:
            current_yaw=me['aim'][0]
            aligned=abs(wrap(aimyaw-current_yaw))<0.15
            for idx,w in enumerate(me['weapons']):
                if not w: continue
                kind=w['kind']; dd=dist(target['pos'],pos)
                if kind=='gun' and dd<min(225,w['stats'].get('range',230)) and aligned and w['reload']<=0:
                    act['fire'][idx]=True
                elif kind=='lightning' and dd<min(42,w['stats'].get('range',45)) and aligned and energy>15:
                    act['fire'][idx]=True
                elif kind=='laser' and dd<min(255,w['stats'].get('range',280)) and not recovering:
                    # Hold until near full charge, release only when aim is aligned.
                    charge=w.get('charge',0)
                    act['fire'][idx]=charge<.76 or not aligned
                elif kind=='mortar' and 32<dd<170 and aligned and not recovering:
                    speed=w['stats'].get('speed',72)
                    grav=w['stats'].get('gravity',22)
                    val=clamp(dd*grav/(speed*speed),0,.98)
                    act['aim'][1]=max(act['aim'][1],.5*math.asin(val)+.035)
                    act['fire'][idx]=True
        if me['weapons'][0] and me['weapons'][0]['kind']=='gun':
            w=me['weapons'][0]
            if w.get('ammo',12)<4 and not shoot and w.get('reload',0)==0: act['reload']=True
        return act

mf.run(Bot)
