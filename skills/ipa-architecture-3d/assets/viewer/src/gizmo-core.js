/** Pure gizmo helpers.
 *
 *  Kept apart from `gizmo.js` on purpose: that module imports Three.js, and the repo's
 *  unit tests run with no npm install. Anything testable without a browser lives here.
 *
 *  Handedness is part of the contract. The viewer renders the Three.js default
 *  RIGHT-handed frame (+X right, +Y up, +Z toward the viewer); a left-handed export
 *  target must therefore be an explicit, visible choice, not a silent one.
 */

export const AXIS_KEYS=['x','y','z'];

const LABELS={
  'zh-CN':{x:'X 轴',y:'Y 轴',z:'Z 轴',hand:'右手系',handNote:'右手坐标系'},
  'ja-JP':{x:'X 軸',y:'Y 軸',z:'Z 軸',hand:'右手系',handNote:'右手座標系'},
  'en-US':{x:'X axis',y:'Y axis',z:'Z axis',hand:'Right-handed',handNote:'Right-handed coordinate system'},
};

export function gizmoLabels(lang='zh-CN'){
  return LABELS[lang]||LABELS['zh-CN'];
}

/** Screen-space direction of each world axis under a camera orientation.
 *
 *  `basis` is the camera's world inverse rotation stored as three ROWS
 *  ([right, up, backward]). The world +axis endpoint for key K has camera-space
 *  coordinates (basis[0][i], basis[1][i], basis[2][i]) where `i` is K's column index --
 *  rows index camera axes, columns index world axes.
 *
 *  Kept dependency-free and pure so any DOM-side axis caption is derived from the same
 *  maths the 3D axis lines use, and therefore cannot drift from what the reader sees. */
export function axisScreenAngles(basis,lang='zh-CN'){
  const labels=gizmoLabels(lang);
  const out={};
  for(let i=0;i<AXIS_KEYS.length;i++){
    const key=AXIS_KEYS[i];
    // Camera-space position of the world +axis endpoint.
    const x=basis[0][i], y=basis[1][i], z=basis[2][i];
    out[key]={
      x,y,z,
      label:labels[key],
      // Positive z in camera space points BACK toward the viewer, so +z means the
      // axis end is on the near side and reads as solid rather than wireframe.
      towardViewer:z>0,
      // Screen direction: 0 deg = right, counter-clockwise positive.
      angle:Math.atan2(y,x)*180/Math.PI,
    };
  }
  return out;
}
