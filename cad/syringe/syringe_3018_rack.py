"""parametric syringe rack-and-pinion spindle-replacement for a 3018 CNC.

v0.2: built against the machine envelope in ../cnc3018/cnc3018.py (52 mm spindle clamp, sleeve removed);
NOT a metrologically verified scan. all dimensions in mm. output: editable STEP, STLs, assembled STEP, GLB, PNG.
the plug drops into the clamp from above; the lip at z=plug_len rests on the clamp top, which fixes the
height (plug_len=18 puts a 51 mm tip end 60 mm above the bed at CNC z=0, the twin SAFE_H).

v0.2 fixes vs v0.1: servo plate moved 2.6 mm back so the SG90 gearbox top/horn no longer overlap the
pinion (gear_back_y follows from the servo stack), horn pocket kept inside the pinion root circle, and the
servo mock uses real SG90 dimensions.

coordinate convention: spindle/syringe center x=y=0, +z upward.
continuous-rotation 9g servo shaft parallel to y, output toward +y.

3 printable parts: monolithic fixed body, moving rack, pinion.
prototype only: validate servo horn/shaft, syringe head, clamp, travel.
"""
from pathlib import Path
import math, json, zipfile
from dataclasses import dataclass, asdict
import cadquery as cq
from cadquery import exporters
import numpy as np
import trimesh
from PIL import Image

@dataclass
class Params:
    # 3018 / 52 mm clamp observed in JacobFV/labrat CAD
    spindle_plug_od: float = 50.2  # intentionally user-supplied, 0.9mm radial space in 52 mm mount
    machine_clamp_id: float = 52.0
    plug_len: float = 18.0  # lip sits on the clamp top: sets tip height (see docstring)
    plug_clearance: float = 0.3
    # disposable syringe body; 80mm assumes just barrel, not nozzle
    syringe_barrel_od: float = 20.2
    syringe_barrel_len: float = 80.0
    syringe_bore_clearance: float = 0.55
    syringe_body_top_z: float = 49.0
    syringe_head_d: float = 14.0  # assumed: change after measuring
    syringe_head_thickness: float = 3.0
    syringe_rod_d: float = 7.6  # plunger rod envelope (round mock; real rods are a + section)
    syringe_flange_d: float = 24.2  # finger flange, worst-case (round) envelope
    syringe_plunger_protrusion: float = 35.0 # assumed at unpressed position
    syringe_stroke: float = 25.0 # illustrative travel, not full syringe spec
    # SG90 / MG90S-class, indicative envelope
    servo_case_long: float = 22.8
    servo_case_short: float = 12.4
    servo_case_y: float = 23.0  # indicative depth along y, not mounting tabs
    servo_tab_hole_pitch: float = 28.0
    servo_tab_hole_d: float = 2.1
    servo_clearance: float = 0.5
    servo_shaft_from_case_left: float = 5.8
    # gear/rack
    gear_module: float = 1.5
    gear_teeth: int = 12
    gear_pressure_angle: float = 20.0
    gear_phase_deg: float = 5.0  # keyed static meshing at travel=0
    gear_width: float = 5.5
    gear_back_y: float = -3.0  # pinion face on the horn side; see SERVO_* stack below
    horn_arm_half: float = 6.8  # trim the single-arm horn to <= this from its centre
    horn_arm_w: float = 5.6
    horn_pocket_depth: float = 1.8
    rack_width: float = 6.0
    rack_root_x: float = 20.5
    rack_min_z: float = 74.0
    rack_max_z: float = 159.0
    rack_tooth_min_z: float = 74.0
    rack_tooth_max_z: float = 151.0
    rack_tooth_backlash: float = 0.2
    guide_clearance: float = 0.55
    servo_shaft_z: float = 102.0
    # adapter features
    top_collar_outer_d: float = 32.0
    top_collar_end_z: float = 49.0
    barrel_set_screw_d: float = 2.8 # pilot to drill/tap for M3; brass insert preferable
    plunger_bridge_z: float = 81.0
    plunger_bridge_gap: float = 3.7
    plunger_bridge_t: float = 2.7
    # default component simplifications for visualization
    plate_y0: float = -17.6
    plate_y1: float = -12.6  # servo tabs bear on this face
    rack_rib_x: float = 17.0
    guide_wall_x0: float = 13.0  # must clear the syringe finger flange as it drops in from the top

P = Params()
HERE = Path(__file__).resolve().parent
STL = HERE / 'stl'; STL.mkdir(exist_ok=True)
STEP = HERE / 'step'; STEP.mkdir(exist_ok=True)

# helper primitives: centered boxes and z vertical cylinders
def box(x0,x1,y0,y1,z0,z1):
    assert x1>x0 and y1>y0 and z1>z0,(x0,x1,y0,y1,z0,z1)
    return cq.Workplane('XY').box(x1-x0,y1-y0,z1-z0).translate(((x0+x1)/2,(y0+y1)/2,(z0+z1)/2))

def cyl(r,z0,z1,x=0,y=0):
    return cq.Workplane('XY').circle(r).extrude(z1-z0).translate((x,y,z0))

def through_y(x,z,r,y0,y1):
    return cq.Workplane('XZ',origin=(0,y1,0)).center(x,z).circle(r).extrude(y1-y0)

def through_x(y,z,r,x0,x1):
    return cq.Workplane('YZ',origin=(x0,0,0)).center(y,z).circle(r).extrude(x1-x0)

# pinion: involute-flanked teeth sampled as one contiguous 2d profile.
# no actual servo spline: attach to the factory horn through the screw and pocket.
def involute_outline(p):
    n, m = p.gear_teeth, p.gear_module
    rp=n*m/2
    ra=rp+m
    rf=rp-1.25*m
    rb=rp*math.cos(math.radians(p.gear_pressure_angle))
    def inv(rad):
        t=math.sqrt(max(0,(rad/rb)**2-1))
        return t-math.atan(t)
    ip=inv(rp)
    half_pitch_tooth=math.pi/(2*n)
    def halfang(rad): return half_pitch_tooth+ip-inv(max(rb,rad))
    out=[]
    for k in range(n):
        c=2*math.pi*k/n
        # tooth root chord valley, left flank, tip, right flank
        outline=[(-math.pi/n,rf),(-halfang(rb)-0.025,rf),(-halfang(rb),rb)]
        for j in range(1,9):
            r=rb+(ra-rb)*j/8
            outline.append((-halfang(r),r))
        for j in range(1,6):
            a=-halfang(ra)+2*halfang(ra)*j/5
            outline.append((a,ra))
        for j in range(7,-1,-1):
            r=rb+(ra-rb)*j/8
            outline.append((halfang(r),r))
        outline += [(halfang(rb)+0.025,rf)]
        for a,r in outline:
            th=a+c
            out.append((r*math.cos(th),r*math.sin(th)))
    return out

# SG90 / MG90S stack along the shaft (+y), measured on a stock TowerPro SG90; tabs bear on plate_y1.
SG90 = dict(tab_t=2.5, above_tab=4.3, spline_above_tab=8.3, below_tab=15.9, tab_len=32.3,
            horn_over_spline=0.6, horn_arm_t=1.6)
SERVO_TAB_TOP_Y = P.plate_y1 + SG90['tab_t']
SERVO_HOUSING_TOP_Y = SERVO_TAB_TOP_Y + SG90['above_tab']
SERVO_SPLINE_TOP_Y = SERVO_TAB_TOP_Y + SG90['spline_above_tab']
HORN_TOP_Y = SERVO_SPLINE_TOP_Y + SG90['horn_over_spline']
# the trimmed horn arm must sit inside the pinion pocket, and the pinion must clear the gearbox top
assert P.gear_back_y <= HORN_TOP_Y - SG90['horn_arm_t'] + 0.05, (P.gear_back_y, HORN_TOP_Y)
assert P.gear_back_y + P.horn_pocket_depth >= HORN_TOP_Y - 0.05, (P.gear_back_y, HORN_TOP_Y)
assert P.gear_back_y - SERVO_HOUSING_TOP_Y >= 2.0, 'pinion too close to the servo gearbox'

PITCH_R = P.gear_teeth*P.gear_module/2
assert P.syringe_rod_d/2+0.8 < P.syringe_head_d/2-1.0, 'fork slot would let the plunger head slip through'
assert P.syringe_flange_d/2+0.5 <= P.guide_wall_x0, 'finger flange cannot pass the rack guide wall'
CLAMP_H = 26.0      # 3018 spindle clamp height (cnc3018.py HOLDER_Y)
NOZZLE_LEN = 10.0   # barrel bottom -> luer nozzle end
assert P.horn_arm_half < PITCH_R - 1.25*P.gear_module, 'horn pocket would cut into the tooth roots'
# rack pitch line is slightly forward of base face, gear center at x=rack_pitch_x+gear_pitch_r
RACK_PITCH_X = P.rack_root_x + P.gear_module*1.25
GEAR_X = RACK_PITCH_X + PITCH_R + P.rack_tooth_backlash

# generated gear uses XY 2d gear polygon then rotate to XZ (shaft along Y).
def pinion():
    pts=involute_outline(P)
    # profile in x-z with axis at (GEAR_X,SERVO_SHAFT_Z), width along y
    # Workplane XZ extrudes -y; start at y=GEAR_BACK+width
    g = (cq.Workplane('XZ',origin=(GEAR_X,P.gear_back_y+P.gear_width,P.servo_shaft_z))
           .polyline(pts).close().extrude(P.gear_width))
    # central 3mm through screw, oversized circular horn hub recess facing -y
    g=g.cut(through_y(GEAR_X,P.servo_shaft_z,1.55,P.gear_back_y-1,P.gear_back_y+P.gear_width+1))
    # 7.6mm dia horn hub seat on servo/back face
    g=g.cut(through_y(GEAR_X,P.servo_shaft_z,3.85,P.gear_back_y-.1,P.gear_back_y+P.horn_pocket_depth))
    # pocket for the trimmed single horn arm (both directions so either trim side fits)
    g=g.cut(box(GEAR_X-P.horn_arm_half,GEAR_X+P.horn_arm_half,P.gear_back_y-.1,P.gear_back_y+P.horn_pocket_depth,
                P.servo_shaft_z-P.horn_arm_w/2,P.servo_shaft_z+P.horn_arm_w/2))
    # align tooth gap at initial rack position (with small printer backlash)
    return g.rotate((GEAR_X,0,P.servo_shaft_z),(GEAR_X,1,P.servo_shaft_z),P.gear_phase_deg)

# rack teeth: 1.5 mod rack; each tooth trapezoid, addendum ~1.5,
# root is x=rack_root_x, tip x=rack_root_x+2.25.
# rotate force direction: rack teeth point +x, pair with gear at +x.
def rack():
    x0 = P.rack_rib_x
    x1 = P.rack_root_x
    r = box(x0,x1,-P.rack_width/2,P.rack_width/2,P.rack_min_z,P.rack_max_z)
    pitch = math.pi*P.gear_module
    # rack tooth has 1.5mm addendum, 1.875 root depth, with slight clearance
    face_x=P.rack_root_x+P.gear_module*2.25
    z=P.rack_tooth_min_z+pitch*0.60
    while z + pitch*.49 < P.rack_tooth_max_z:
        # CAD poly in x,z projected into XZ, extrude along Y
        half_root=pitch/4+1.25*P.gear_module*math.tan(math.radians(P.gear_pressure_angle))
        half_tip=pitch/4-1.0*P.gear_module*math.tan(math.radians(P.gear_pressure_angle))
        pts=[(x1-0.06,z-half_root),(face_x,z-half_tip),(face_x,z+half_tip),(x1-0.06,z+half_root)]
        tooth=cq.Workplane('XZ',origin=(0,P.rack_width/2,0)).polyline(pts).close().extrude(P.rack_width)
        r=r.union(tooth)
        z+=pitch
    # fork attached near plunger thumb: bilateral plates capture button flange
    # lower and upper plates separated by approx 3.7mm. Heads are measured user-side.
    z0=P.plunger_bridge_z
    t=P.plunger_bridge_t
    z1=z0+t+P.plunger_bridge_gap
    fork_pts=[(-8.5,-9),(8,-9),(8,-3),(x0+0.3,-3),
              (x0+0.3,3),(8,3),(8,9),(-8.5,9)]
    r=r.union(cq.Workplane('XY',origin=(0,0,z0)).polyline(fork_pts).close().extrude(t))
    r=r.union(cq.Workplane('XY',origin=(0,0,z1)).polyline(fork_pts).close().extrude(t))
    # side rails on outer y edges for a captive U-shaped coupler
    for sy in [-1,1]:
        ya,yb=sorted([sy*7.1,sy*9.0])
        r=r.union(box(7.5,10.5,ya,yb,z0,z1+t))
    # entry slots provide shaft clearance while flange (d14) stays between forks
    for lo,hi in [(z0-.2,z0+t+.2),(z1-.2,z1+t+.2)]:
        r=r.cut(box(-9,P.syringe_rod_d/2+0.8,-4.6,4.6,lo,hi))
    return r

# ONE-PART fixed body — 50.2 mm plug, barrel bore, collar, vertical rack guide,
# continuous-servo saddle + tab screw holes. prints upright w/ supports or on side.
def body():
    b=cyl(P.spindle_plug_od/2,0,P.plug_len)
    # shallow lip stops adapter sliding down through spindle collar
    b=b.union(cyl(P.spindle_plug_od/2+2.2,P.plug_len,P.plug_len+3.0))
    b=b.union(cyl(P.top_collar_outer_d/2,P.plug_len+2.7,P.top_collar_end_z))
    b=b.cut(cyl((P.syringe_barrel_od+P.syringe_bore_clearance)/2,-1,P.top_collar_end_z+1))
    # axial syringe retention: 2x cross M3 pilot holes in top collar
    b=b.cut(through_x(0,46.1,P.barrel_set_screw_d/2,-18,-9))
    b=b.cut(through_x(0,39.0,P.barrel_set_screw_d/2,9,26))
    # guide backside and side rails; open teeth-facing side toward +x
    wall_x0=P.guide_wall_x0; wall_x1=P.rack_rib_x-P.guide_clearance
    b=b.union(box(wall_x0,wall_x1,-7.3,7.3,41,144))
    # longitudinal fork escape window: fork passes from rack at +x to plunger at x=0
    b=b.cut(box(wall_x0-0.5,wall_x1+0.2,-3.3,3.3,51,96))
    cheek_x1=P.rack_root_x+1.2
    for yy0,yy1 in [(-7.3,-P.rack_width/2-P.guide_clearance),
                      (P.rack_width/2+P.guide_clearance,7.3)]:
        b=b.union(box(wall_x1-0.05,cheek_x1,yy0,yy1,58,144))
    # narrow cap/lead-in lip to hold rack laterally without blocking teeth
    # front teeth kept clear; rack removable by sliding from top
    # servo plate integrated with guide via negative-y gusset
    b=b.union(box(wall_x0+0.5,wall_x0+8,P.plate_y0,-7,44,93))
    # cross bar joins plate and guide for rigidity, located behind path
    b=b.union(box(wall_x0+3,wall_x0+14,P.plate_y0,-7,76,83))
    b=b.union(box(wall_x0+6.8,GEAR_X+25.4,P.plate_y0,P.plate_y1,83,123))
    # servo rectangular pass-through: x body 23.2..46, z 95.5..108
    xcase0=GEAR_X-P.servo_shaft_from_case_left
    xcase1=xcase0+P.servo_case_long
    zcase0=P.servo_shaft_z-P.servo_case_short/2
    zcase1=P.servo_shaft_z+P.servo_case_short/2
    clr=P.servo_clearance/2
    b=b.cut(box(xcase0-clr,xcase1+clr,P.plate_y0-1,P.plate_y1+1,zcase0-clr,zcase1+clr))
    # servo ears at x gear center + approx (long/2 - shaft offset) +/-14
    mid=xcase0+P.servo_case_long/2
    for x in [mid-P.servo_tab_hole_pitch/2,mid+P.servo_tab_hole_pitch/2]:
        b=b.cut(through_y(x,P.servo_shaft_z,P.servo_tab_hole_d/2,P.plate_y0-1,P.plate_y1+1))
    # tie-through holes at top for optional strap or reinforcement
    for z in [115,120]:
        b=b.cut(through_y(wall_x0+6.5,z,1.65,P.plate_y0-1,-9))
    return b

# mechanical surrogate solids for placement/visualization only
# syringe plunger starts 36mm above barrel top and moves down 25mm.
def mock_syringe():
    bottom=P.syringe_body_top_z-P.syringe_barrel_len
    barrel=(cyl(P.syringe_barrel_od/2,bottom,P.syringe_body_top_z)
           .cut(cyl(max(1,(P.syringe_barrel_od/2-1.8)),bottom-.1,P.syringe_body_top_z+.1)))
    barrel=barrel.union(cyl(P.syringe_flange_d/2, P.syringe_body_top_z, P.syringe_body_top_z+1.8))
    # conical bottom + luer slip nozzle (the disposable pipette tip presses onto it)
    barrel=barrel.union(cq.Solid.makeCone(P.syringe_barrel_od/2,2.6,4,cq.Vector(0,0,bottom),cq.Vector(0,0,-1)))
    barrel=barrel.union(cq.Solid.makeCone(2.1,1.9,NOZZLE_LEN-4,cq.Vector(0,0,bottom-4),cq.Vector(0,0,-1)))
    return barrel

def mock_plunger(travel=0):
    zhead=P.syringe_body_top_z+P.syringe_plunger_protrusion-travel
    return cyl(P.syringe_rod_d/2,P.syringe_body_top_z-.6,zhead).union(cyl(P.syringe_head_d/2,zhead,zhead+P.syringe_head_thickness))

def mock_servo():
    x0=GEAR_X-P.servo_shaft_from_case_left
    x1=x0+P.servo_case_long
    z0=P.servo_shaft_z-P.servo_case_short/2
    z1=P.servo_shaft_z+P.servo_case_short/2
    # inserted from the front: body through the plate cutout, tabs on the plate's front face, shaft +y
    case=box(x0,x1,P.plate_y1-SG90['below_tab'],SERVO_HOUSING_TOP_Y,z0,z1)
    mid=(x0+x1)/2
    case=case.union(box(mid-SG90['tab_len']/2,mid+SG90['tab_len']/2,P.plate_y1,SERVO_TAB_TOP_Y,z0,z1))
    # output boss, spline and (trimmed) horn
    case=case.union(through_y(GEAR_X,P.servo_shaft_z,2.9,SERVO_HOUSING_TOP_Y-.01,SERVO_SPLINE_TOP_Y-2.0))
    case=case.union(through_y(GEAR_X,P.servo_shaft_z,2.4,SERVO_SPLINE_TOP_Y-2.0,SERVO_SPLINE_TOP_Y))
    case=case.union(through_y(GEAR_X,P.servo_shaft_z,3.5,SERVO_SPLINE_TOP_Y-2.6,HORN_TOP_Y-SG90['horn_arm_t']))
    arm=box(GEAR_X-P.horn_arm_half+.3,GEAR_X+P.horn_arm_half-.3,HORN_TOP_Y-SG90['horn_arm_t'],HORN_TOP_Y,
            P.servo_shaft_z-P.horn_arm_w/2+.3,P.servo_shaft_z+P.horn_arm_w/2-.3)
    case=case.union(arm.rotate((GEAR_X,0,P.servo_shaft_z),(GEAR_X,1,P.servo_shaft_z),P.gear_phase_deg))
    return case

def mock_clamp():
    # surrogate of the 26 mm tall 52 mm clamp in ../cnc3018/cnc3018.py; the plug lip rests on its top face
    z0,z1=P.plug_len-CLAMP_H,P.plug_len
    mount=cyl(P.machine_clamp_id/2+7.3,z0,z1).cut(cyl(P.machine_clamp_id/2,z0-1,z1+1))
    # rear block: clamp sits behind spindle (+y) in machine frame
    mount=mount.union(box(-34,34,P.machine_clamp_id/2+.5,32,z0,z1))
    return mount

parts={'mount_body':body(),'rack':rack(),'pinion':pinion()}
mocks={'syringe':mock_syringe(),'plunger':mock_plunger(),'servo':mock_servo(),'clamp_proxy':mock_clamp()}
for name,s in parts.items():
    cq.exporters.export(s,str(STEP / f'{name}.step'))
    cq.exporters.export(s,str(STL / f'{name}.stl'),tolerance=.045,angularTolerance=.2)

ass=cq.Assembly(name='cnc3018_syringe_actuator')
colors={'mount_body':cq.Color(0.15,0.43,0.75),'rack':cq.Color(0.95,0.54,0.12),
        'pinion':cq.Color(0.86,0.28,0.22),'syringe':cq.Color(.78,.79,.84,.5),
        'plunger':cq.Color(.70,.71,.74),'servo':cq.Color(.10,.21,.47,.8),
        'clamp_proxy':cq.Color(.27,.3,.34,.30)}
for n,s in {**parts,**mocks}.items():
    ass.add(s,name=n,color=colors[n])
ass.save(str(STEP/'assembly.step'))

# Compute bounds and rough pairwise volumes of critical contacts.
report={'params':asdict(P), 'pitch_r':PITCH_R, 'gear_center_x':GEAR_X,
        'rack_pitch_x':RACK_PITCH_X,'ref_machine_clamp_d':52.0,
        'machine_reference':'JacobFV/labrat/cad/cnc3018/cnc3018.py',
        'parts':{},'fit_checks':{}}
for n,s in parts.items():
    solid=s.val(); bb=solid.BoundingBox()
    report['parts'][n]={'valid':solid.isValid(),'volume_cm3':round(solid.Volume()/1000,2),
                        'bounds_mm':[[round(bb.xmin,2),round(bb.ymin,2),round(bb.zmin,2)],
                                     [round(bb.xmax,2),round(bb.ymax,2),round(bb.zmax,2)]]}
for na,nb in [('mount_body','rack'),('mount_body','pinion'),('rack','pinion')]:
    try:
        volume=parts[na].intersect(parts[nb]).val().Volume()
        report['fit_checks'][f'{na}:{nb}_overlap_mm3']=round(volume,3)
    except Exception as e: report['fit_checks'][f'{na}:{nb}_overlap']='error: '+str(e)
# verify rack+pinion across the requested illustrative 25 mm plunge
sweep=[]
for travel_mm in np.linspace(0,P.syringe_stroke,11):
    rack_at=parts['rack'].translate((0,0,-float(travel_mm)))
    degrees=-math.degrees(float(travel_mm)/PITCH_R)
    gear_at=parts['pinion'].rotate((GEAR_X,0,P.servo_shaft_z),(GEAR_X,1,P.servo_shaft_z),degrees)
    vol1=rack_at.intersect(parts['mount_body']).val().Volume()
    vol2=rack_at.intersect(gear_at).val().Volume()
    sweep.append({'down_mm':round(float(travel_mm),2),
                  'rack_housing_overlap_mm3':round(vol1,3),
                  'rack_gear_overlap_mm3':round(vol2,3)})
report['kinematic_sweep']=sweep
# static checks against the purchased parts (servo, syringe) and the machine clamp
for na,nb in [('servo','mount_body'),('servo','pinion'),('servo','rack'),('syringe','mount_body'),
              ('syringe','rack'),('clamp_proxy','mount_body')]:
    A={**parts,**mocks}[na]; B={**parts,**mocks}[nb]
    report['fit_checks'][f'{na}:{nb}_overlap_mm3']=round(A.intersect(B).val().Volume(),3)
# the barrel (flange first... last) is dropped in from the top: its flange envelope must clear the body
path=cyl(P.syringe_flange_d/2,P.syringe_body_top_z+0.01,P.rack_max_z+20)
report['fit_checks']['flange_insertion_path_overlap_mm3']=round(path.intersect(parts['mount_body']).val().Volume(),3)
# plunger rod/head moves with the rack: no collision anywhere in the stroke
report['fit_checks']['plunger:rack_max_overlap_mm3']=round(max(
    mock_plunger(t).intersect(parts['rack'].translate((0,0,-t))).val().Volume() for t in np.linspace(0,P.syringe_stroke,6)),3)
# plunger head must stay captured between the fork plates over the whole stroke
z0=P.plunger_bridge_z; z1=z0+P.plunger_bridge_t+P.plunger_bridge_gap
head0=P.syringe_body_top_z+P.syringe_plunger_protrusion
report['fit_checks']['head_in_fork_gap']=bool(z0+P.plunger_bridge_t<=head0 and head0+P.syringe_head_thickness<=z1)
report['fit_checks']['fork_above_barrel_at_full_stroke_mm']=round(z0-P.syringe_stroke-(P.syringe_body_top_z+1.8),2)
bad=[k for k,v in report['fit_checks'].items() if (isinstance(v,float) and k.endswith('mm3') and v>0.5
     and not k.startswith('clamp_proxy')) or v is False or (k.startswith('fork_above') and v<0.5)]
report['nozzle_end_z']=P.syringe_body_top_z-P.syringe_barrel_len-NOZZLE_LEN
report['servo_stack_y']={'tab_top':SERVO_TAB_TOP_Y,'housing_top':SERVO_HOUSING_TOP_Y,
                         'spline_top':SERVO_SPLINE_TOP_Y,'horn_top':HORN_TOP_Y}
(HERE/'report.json').write_text(json.dumps(report,indent=2))

# export color-coded glb assembly for browser/3d viewer
scene=trimesh.Scene()
for n,s in {**parts,**mocks}.items():
    tmp=STL / f'_{n}_temp.stl'
    if n in mocks: cq.exporters.export(s,str(tmp),tolerance=.11,angularTolerance=.3)
    else: tmp=STL / f'{n}.stl'
    mesh=trimesh.load(tmp,force='mesh',process=False)
    rgba=np.array(colors[n].toTuple()[:3])*255
    mesh.visual.face_colors=[int(a) for a in rgba]+[155 if n=='clamp_proxy' else 245]
    scene.add_geometry(mesh,geom_name=n,node_name=n)
    if n in mocks: tmp.unlink()
scene.export(HERE/'assembly.glb')

# shaded multi-angle preview (lambert + specular per triangle, painter's sort), plunger at mid stroke
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
mid=P.syringe_stroke/2
posed={**mocks,**parts}
posed['rack']=parts['rack'].translate((0,0,-mid)); posed['plunger']=mock_plunger(mid)
posed['pinion']=parts['pinion'].rotate((GEAR_X,0,P.servo_shaft_z),(GEAR_X,1,P.servo_shaft_z),-math.degrees(mid/PITCH_R))
meshes={}
for n,sh in posed.items():
    tmp=STL/f'_{n}_r.stl'; cq.exporters.export(sh,str(tmp),tolerance=.08,angularTolerance=.25)
    meshes[n]=trimesh.load(tmp,force='mesh'); tmp.unlink()
def shade_view(ax,azim,elev,title):
    a,e=math.radians(azim),math.radians(elev)
    d=np.array([math.cos(e)*math.cos(a),math.cos(e)*math.sin(a),math.sin(e)])  # toward the camera, z up
    right=np.cross([0,0,1],d); right/=np.linalg.norm(right); up=np.cross(d,right)
    L=np.array([0.4,-0.5,0.8]); L/=np.linalg.norm(L); H=(L+d)/np.linalg.norm(L+d)
    P2,C,D=[],[],[]
    for n,m in meshes.items():
        base=np.array(colors[n].toTuple()[:3]); alpha=0.25 if n=='clamp_proxy' else 0.55 if n=='syringe' else 1.0
        tri=m.triangles; nn=m.face_normals; vis=(nn@d)>-0.02
        tri,nn=tri[vis],nn[vis]
        k=0.30+0.62*np.clip(nn@L,0,1)+0.10*np.clip(nn@np.array([0,0,1]),0,1)
        col=np.clip(base[None]*k[:,None]+0.35*np.clip(nn@H,0,1)[:,None]**30,0,1)
        col=np.concatenate([col,np.full((len(col),1),alpha)],1)
        P2.append(np.stack([tri@right,tri@up],-1)); C.append(col); D.append((tri@d).mean(1))
    P2=np.concatenate(P2); C=np.concatenate(C); o=np.argsort(np.concatenate(D))
    ax.add_collection(PolyCollection(P2[o],facecolors=C[o],edgecolors=C[o],linewidths=0.1))
    lo,hi=P2.reshape(-1,2).min(0),P2.reshape(-1,2).max(0); c=(lo+hi)/2; h=(hi-lo).max()/2*1.05
    ax.set_xlim(c[0]-h,c[0]+h); ax.set_ylim(c[1]-h,c[1]+h); ax.set_aspect('equal'); ax.axis('off')
    ax.set_title(title,fontsize=11)
fig,axs=plt.subplots(2,2,figsize=(12,12),facecolor='#F4F5F7')
for ax,(az,el,t) in zip(axs.flat,[(-60,22,'front-right iso'),(-120,18,'front-left iso'),
                                   (0,4,'right (rack + pinion)'),(60,30,'rear iso')]):
    ax.set_facecolor('#F4F5F7'); shade_view(ax,az,el,t)
fig.suptitle(f'3018 syringe rack-and-pinion head v0.2  (plunger at {mid:.1f} of {P.syringe_stroke:.0f} mm)',fontsize=14)
fig.tight_layout(); fig.savefig(HERE/'preview.png',dpi=110); plt.close(fig)

# mock purchased parts as meshes for ../cnc3018/cnc3018.py (twin + full-bench renders)
for n in ('syringe','plunger','servo'):
    cq.exporters.export(mocks[n],str(STL / f'mock_{n}.stl'),tolerance=.06,angularTolerance=.25)
print(json.dumps({k:report[k] for k in ('fit_checks','nozzle_end_z','servo_stack_y')},indent=2))

assert not bad, f'fit checks failed: {bad}'

# one print plate (3MF, 220x220 bed) in print orientation: body upright, rack and pinion flat on a y face
plate=trimesh.Scene()
orient={'mount_body':np.eye(4),
        'rack':trimesh.transformations.rotation_matrix(math.pi/2,[1,0,0]),
        'pinion':trimesh.transformations.rotation_matrix(math.pi/2,[1,0,0])}
spots={'mount_body':(-35,0),'rack':(45,-20),'pinion':(45,45)}
for n,M in orient.items():
    m=trimesh.load(STL/f'{n}.stl',force='mesh'); m.apply_transform(M)
    lo,hi=m.bounds; m.apply_translation([spots[n][0]-(lo[0]+hi[0])/2,spots[n][1]-(lo[1]+hi[1])/2,-lo[2]])
    plate.add_geometry(m,geom_name=n,node_name=n)
lo,hi=np.vstack([g.bounds for g in plate.geometry.values()]).min(0),np.vstack([g.bounds for g in plate.geometry.values()]).max(0)
assert (hi[:2]-lo[:2]).max()<215, f'plate does not fit 220x220: {lo} {hi}'
plate.export(HERE/'plate_syringe.3mf')
print('plate_syringe.3mf', (hi-lo).round(1))
