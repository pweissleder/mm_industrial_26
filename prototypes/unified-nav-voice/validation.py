"""Independent evidence: compare centred/CCW JSON footprints to original SVG."""
import math
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from shapely.geometry import Polygon, box
from shapely.affinity import affine_transform, rotate, translate
from data import load_building, read_member, building_index
from errors import ModelError

@lru_cache(maxsize=8)
def validate_footprints(building_id):
    building=load_building(building_id)
    if building['building']['source']!='procedural':
        return {'verified':False,'reason':'SVG convention validation currently limited to procedural factory/campus rectangles'}
    count=0;maximum=0.0
    directory=building_index()[building_id].rsplit('/',1)[0]
    for floor in building['floors']:
        spaces=[s for s in building['spaces'] if s['floor_id']==floor['id']]
        assets=[a for a in building['assets'] if a['floor_id']==floor['id']]
        document=ET.fromstring(read_member(directory+f"/floor_{floor['level']}.svg"))
        patches={e.attrib.get('id'):e for e in document.iter() if e.attrib.get('id','').startswith('patch_')}
        def patch(number):
            paths=[e for e in patches[f'patch_{number}'].iter() if e.tag.endswith('path')]
            text=paths[0].attrib['d']
            if re.search('[CQAST]',text,re.I): raise ValueError('nonpolygonal patch')
            values=[float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?',text)]
            return Polygon(list(zip(values[::2],values[1::2])))
        try:
            original=Polygon(spaces[0]['polygon']);rendered=patch(2)
            x0,y0,x1,y1=original.bounds;u0,v0,u1,v1=rendered.bounds
            sx=(u1-u0)/(x1-x0);sy=-(v1-v0)/(y1-y0)
            tx=u0-sx*x0;ty=v1-sy*y0
            matrix=[1/sx,0,0,1/sy,-tx/sx,-ty/sy]
            for i,space in enumerate(spaces):
                if Polygon(space['polygon']).hausdorff_distance(affine_transform(patch(i+2),matrix))>1e-5:
                    raise ValueError('Room-to-SVG correspondence failed')
            for i,asset in enumerate(assets):
                f=asset['footprint'];w,d=float(f['width_m']),float(f['depth_m'])
                if not all(math.isfinite(x) and x>0 for x in [w,d]):raise ValueError('Invalid footprint')
                expected=translate(rotate(box(-w/2,-d/2,w/2,d/2),float(f.get('rotation_deg',0)),origin=(0,0)),*asset['position'])
                observed=affine_transform(patch(len(spaces)+i+2),matrix)
                distance=expected.hausdorff_distance(observed)
                maximum=max(maximum,distance);count+=1
                if distance>1e-5:raise ValueError('Asset-to-SVG correspondence failed')
        except (KeyError,ValueError,ZeroDivisionError,IndexError) as error:
            return {'verified':False,'reason':str(error),'assets_checked':count,'max_discrepancy_m':maximum}
    return {'verified':True,'assets_checked':count,'max_discrepancy_m':maximum,
            'position':'center','rotation':'counterclockwise around center in world XY',
            'evidence':'All original SVG room/asset polygons, not a real-world survey'}
