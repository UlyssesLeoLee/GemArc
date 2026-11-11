/** Review cameras: a fixed viewpoint only earns its place if the angle itself carries
 *  architectural meaning. Each rig is defined by WHICH AXIS BECOMES THE SCREEN FLOOR.
 *
 *  Rationale — a generic orbit camera is decoration; a camera is analysis. When you look
 *  straight down at the XZ plane you are reading a *matrix*: one row per X category, one
 *  column per Z category. That only answers "which X/Z cell is this node in", which is a
 *  real review question (collision, placement, cross-cutting placement). Oblique views
 *  answer "how do these clusters sit relative to each other in depth". The rig therefore
 *  declares what it lets you *see* and what it deliberately *hides*, and that claim is
 *  checked against the live layout rather than trusted.
 */

/** World frame is the Three.js default right-handed one: +X right, +Y up, +Z toward the
 *  viewer. `hidden` is the viewing ray: the axis that collapses to a point in the image. */
export const AXIS_ROLES={
  /** Camera above, looking down -Y: X across, Z down the screen, Y becomes thickness. */
  plan:{x:'x',y:'z',hidden:'y'},
  /** Camera on +Z, looking in -Z: X across, Y up, Z is depth. */
  elevation:{x:'x',y:'y',hidden:'z'},
  /** Camera on +X, looking in -X: Z across, Y up, X is depth. */
  section:{x:'z',y:'y',hidden:'x'},
};

/** Each rig answers exactly one review question; `hint` states it in plain words. */
export const RIGS={
  /* ---------------------------------------------------------------- 3D */
  iso:{
    id:'iso',kind:'iso',orthographic:false,
    question:'spatialRelation',
    hint:'The only angle showing all three axes at once: separation, overlap and depth read together.',
    dir:[1,.78,1.14],up:[0,1,0],
  },

  /* ------------------------------------------------- orthographic "views" */
  plan:{
    id:'plan',kind:'plan',orthographic:true,roles:AXIS_ROLES.plan,
    question:'matrixXZ',
    hint:'Reads as an X x Z grid: each cell is one placement decision, and collisions are visible.',
    dir:[0,1,0],up:[0,0,-1],
  },
  elevation:{
    id:'elevation',kind:'elevation',orthographic:true,roles:AXIS_ROLES.elevation,
    question:'stackXY',
    hint:'Reads as an X x Y stack: how layers sit above one another, with depth collapsed.',
    dir:[0,0,1],up:[0,1,0],
  },
  section:{
    id:'section',kind:'section',orthographic:true,roles:AXIS_ROLES.section,
    question:'sectionZY',
    hint:'Reads as a Z x Y section: slices the scene at constant X to compare rows against each other.',
    dir:[1,0,0],up:[0,1,0],
  },
};

export const RIG_IDS=Object.keys(RIGS);

export const QUESTION_TEXT={
  'spatialRelation':{'zh-CN':'空间关系','ja-JP':'空間関係','en-US':'Spatial relation'},
  'matrixXZ':{'zh-CN':'X×Z 矩阵','ja-JP':'X×Z マトリクス','en-US':'X×Z matrix'},
  'stackXY':{'zh-CN':'X×Y 层叠','ja-JP':'X×Y 層構成','en-US':'X×Y stack'},
  'sectionZY':{'zh-CN':'Z×Y 剖面','ja-JP':'Z×Y 断面','en-US':'Z×Y section'},
};

/** Is this rig honest for THIS layout?
 *
 *  Rule: BOTH screen axes must carry information. A flat screen axis still occupies
 *  screen space, so the reader would see a zero-height band labelled with a real category
 *  and read it as a genuine layer -- a fake dimension wearing a real-looking name.
 *
 *  The most damaging case is when the ONLY useful axis is the one the camera erases: the
 *  image then shows a confident, plausible arrangement of nodes whose placement carries
 *  no information at all. Report that explicitly so the UI can explain rather than hide.
 */
export function rigAssessment(rig,layout){
  const a=layout.axes, e=layout.effective;
  if(rig.kind==='iso'){
    // The oblique view is only worth offering when there is depth to look at.
    const depth=layout.depth;
    return {
      valid:depth>=2,
      reasons:{
        tooFlat:depth<2,
        reason:depth<2?'spatial-need-2d':null,
      },
      separates:{x:e.x,y:e.y,z:e.z},
    };
  }
  const {x,y,hidden}=rig.roles;
  const flat=[x,y].filter(axis=>!e[axis]);
  // Every useful axis erased: the picture would be arrangement without meaning.
  const erasesEverything=e[hidden]&&flat.length===2;
  const flatFields=flat.map(axis=>({axis,field:a[axis]?a[axis].field:null,size:a[axis]?a[axis].size:0}));
  return {
    valid:flat.length===0,
    reasons:{
      flatScreenAxes:flatFields,
      screenAxisFlat:flat.length>0,
      hiddenAxisIsOnlyUseful:erasesEverything,
      hiddenAxisField:a[hidden]?a[hidden].field:null,
      hiddenAxisUseful:e[hidden],
    },
    screenAxes:{x,y,hidden},
  };
}

/** Where the camera sits, in normalised scene units. Pure: no THREE dependency, so it
 *  can be unit-tested and reused by both the 3D scene and the 2D preview harness. */
export function cameraPlacement(rig,layout,bounds,aspect=1){
  const cx=(bounds.minX+bounds.maxX)/2, cy=(bounds.minY+bounds.maxY)/2, cz=(bounds.minZ+bounds.maxZ)/2;
  const center=[cx,cy,cz];
  const dx=Math.max(8,bounds.maxX-bounds.minX+10);
  const dy=Math.max(6,bounds.maxY-bounds.minY+9);
  const dz=Math.max(8,bounds.maxZ-bounds.minZ+12);
  if(rig.kind==='iso'){
    // Pull back far enough that the bounding *sphere* fits the 20° half-FOV.
    const radius=Math.sqrt(dx*dx+dy*dy+dz*dz)/2;
    const distance=radius/Math.sin(20*Math.PI/180)*1.05;
    const v=rig.dir, len=Math.hypot(v[0],v[1],v[2]);
    const unit=[v[0]/len,v[1]/len,v[2]/len];
    return {center,position:[cx+unit[0]*distance,cy+unit[1]*distance,cz+unit[2]*distance],distance,up:rig.up};
  }
  // Orthographic: every rig frames the SAME scene box, so switching angles changes
  // only what is occluded — never the scale. That is what makes two angles comparable.
  const halfHeight=Math.max(dy,dz)/2*1.16;
  const halfWidth=halfHeight*Math.max(0.55,aspect);
  // An orthographic camera has no perspective divide, so distance only sets how far
  // OUT the camera sits. It must be large enough to clear the scene box, and it MUST be
  // returned: callers derive near/far from it, and a missing value yields NaN, which
  // Three stores as far=null and silently renders an empty frame.
  const distance=Math.max(dx,dy,dz)*2;
  return {center,position:[cx+rig.dir[0]*distance,cy+rig.dir[1]*distance,cz+rig.dir[2]*distance],
          distance,halfHeight,halfWidth,up:rig.up};
}

/** Which model axes map to screen X and screen Y, resolved against the live layout so
 *  the axis captions in the corner never contradict the actual camera. */
export function screenAxesFor(rig,layout){
  if(rig.kind==='iso')return {x:'x',y:'y',z:'z'};
  const {x,y,hidden}=rig.roles;
  return {x,y,z:hidden};
}

/** Rows/columns actually visible from this angle, for the HUD grid readout. The reader
 *  should be able to count what the camera shows rather than trust a static caption. */
export function rigMatrix(rig,layout){
  if(rig.kind==='iso')return null;
  const {x,y,hidden}=rig.roles;
  const col=layout.axes[x], row=layout.axes[y];
  return {
    columns:{axis:x,field:col.field,size:col.size,effective:layout.effective[x],values:col.values},
    rows:{axis:y,field:row.field,size:row.size,effective:layout.effective[y],values:row.values},
    hidden:{axis:hidden,field:layout.axes[hidden].field,effective:layout.effective[hidden]},
    cells:layout.effective[x]&&layout.effective[y]?col.size*row.size:0,
  };
}
