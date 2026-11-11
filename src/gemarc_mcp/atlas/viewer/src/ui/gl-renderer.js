// Raw WebGL glue of the stage (no libraries, no page access): compiles five small programs and draws what scene-buffers.js produced.
// Part shapes are drawn instanced, one draw call per shape (a shared triangle template plus per-instance offset, scale and colour), through WebGL 2
// or, on a WebGL 1 context, through ANGLE_instanced_arrays; a context with neither draws the flat mesh of makeBuffers. Edges are one line
// buffer, 4D event energy is one point buffer, the state halos one more. The caller passes the context; this module never creates one.
//
// Everything here is deterministic: the only time input is the `clock` number the caller supplies (a frame count in still mode).
//
// Source convention (the release bundler relies on it): single-line imports, only `export const|function|class`.

export const GL_THEMES = { dark: { bg: [0.035, 0.05, 0.11] }, light: { bg: [0.96, 0.96, 0.95] } };
export const GL_LIGHT = [-0.4, 0.9, -0.5];
const GL_SOFTWARE = /swiftshader|software|microsoft basic/i;

const GL_VS_MESH = 'attribute vec3 aPos;attribute vec3 aNormal;attribute vec3 aOff;attribute float aScl;attribute vec4 aTint;uniform mat4 uVP;varying vec3 vN;varying vec4 vC;varying vec3 vW;void main(){vec3 w=aPos*aScl+aOff;gl_Position=uVP*vec4(w,1.0);vN=aNormal;vC=aTint;vW=w;}'; // diag: shader source
const GL_FS_MESH = 'precision mediump float;uniform vec3 uL;uniform vec3 uE;varying vec3 vN;varying vec4 vC;varying vec3 vW;void main(){vec3 n=normalize(vN);vec3 v=normalize(uE-vW);if(dot(n,v)<0.0)n=-n;float d=max(dot(n,normalize(uL)),0.0);float f=pow(1.0-max(dot(n,v),0.0),2.0);float s=pow(max(dot(reflect(-normalize(uL),n),v),0.0),24.0);vec3 c=vC.rgb*(0.5+0.5*d)+vec3(0.55,0.75,1.0)*f*0.45+vec3(1.0)*s*0.6;gl_FragColor=vec4(c,vC.a*(0.42+0.5*f+0.2*d));}'; // diag: shader source
const GL_VS_LINE = 'attribute vec3 aPos;attribute vec4 aTint;uniform mat4 uVP;varying vec4 vC;void main(){gl_Position=uVP*vec4(aPos,1.0);vC=aTint;}'; // diag: shader source
const GL_FS_LINE = 'precision mediump float;varying vec4 vC;void main(){gl_FragColor=vC;}'; // diag: shader source
// Dots and halos are camera-facing quads (two triangles per sprite, expanded on the CPU): the corner runs -1..1 and the vertex shader moves it in pixels.
const GL_VS_DOT = 'attribute vec3 aPos;attribute vec4 aTint;attribute vec2 aCorner;uniform mat4 uVP;uniform float uPS;uniform vec2 uView;varying vec4 vC;varying vec2 vQ;void main(){vec4 p=uVP*vec4(aPos,1.0);p.xy+=aCorner*uPS/uView*p.w;gl_Position=p;vC=aTint;vQ=aCorner;}'; // diag: shader source
const GL_FS_DOT = 'precision mediump float;varying vec4 vC;varying vec2 vQ;void main(){float r=length(vQ);if(r>1.0)discard;float c=pow(1.0-r,1.6);gl_FragColor=vec4(mix(vC.rgb,vec3(1.0),c*0.35),clamp(c*1.2,0.0,1.0)*vC.a);}'; // diag: shader source
const GL_VS_HALO = 'attribute vec3 aPos;attribute vec4 aTint;attribute vec2 aSz;attribute vec2 aCorner;uniform mat4 uVP;uniform float uK;uniform float uT;uniform vec2 uView;varying vec4 vC;varying float vP;varying vec2 vQ;void main(){vec4 p=uVP*vec4(aPos,1.0);float br=0.88+0.12*sin(uT*3.0+aSz.y);float px=max(2.0,aSz.x*br*uK/max(p.w,0.1));p.xy+=aCorner*px/uView*p.w;gl_Position=p;vC=aTint;vP=uT*1.4+aSz.y*0.37;vQ=aCorner;}'; // diag: shader source
const GL_FS_HALO = 'precision mediump float;varying vec4 vC;varying float vP;varying vec2 vQ;void main(){float r=length(vQ);if(r>1.0)discard;float core=pow(1.0-r,2.2);float ph=vP-floor(vP);float ring=exp(-pow((r-ph)*7.0,2.0))*(1.0-ph);float a=clamp((core*0.75+ring*0.6)*vC.a,0.0,1.0);gl_FragColor=vec4(mix(vC.rgb,vec3(1.0),core*0.15),a);}'; // diag: shader source

function glCompile(gl, type, src) {
  const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error('shader: ' + gl.getShaderInfoLog(s));
  return s;
}
function glProgram(gl, vs, fs) {
  const p = gl.createProgram(); gl.attachShader(p, glCompile(gl, gl.VERTEX_SHADER, vs)); gl.attachShader(p, glCompile(gl, gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error('link: ' + gl.getProgramInfoLog(p));
  return p;
}

/**
 * Prepare a context: programs, attribute and uniform locations, buffers and the instancing path. Returns the renderer state or throws (the
 * caller catches and shows the labelled fallback). `info` names the driver: { renderer, version, software, instancing }.
 */
export function glInit(gl) {
  const pm = glProgram(gl, GL_VS_MESH, GL_FS_MESH); const pl = glProgram(gl, GL_VS_LINE, GL_FS_LINE); const pd = glProgram(gl, GL_VS_DOT, GL_FS_DOT); const ph = glProgram(gl, GL_VS_HALO, GL_FS_HALO);
  const at = (p, n) => gl.getAttribLocation(p, n); const un = (p, n) => gl.getUniformLocation(p, n);
  const isTwo = typeof gl.drawArraysInstanced === 'function'; // only a WebGL 2 context has instancing built in
  const ext = isTwo ? null : gl.getExtension('ANGLE_instanced_arrays');
  const instancing = isTwo ? 'webgl2' : ext ? 'extension' : 'none';
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = String(dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
  const names = ['pos', 'nrm', 'off', 'scl', 'col', 'line', 'pt'];
  const buf = {}; for (const n of names) buf[n] = gl.createBuffer();
  return {
    gl, pm, pl, pd, ph, buf, instancing,
    divisor: isTwo ? (loc, d) => gl.vertexAttribDivisor(loc, d) : ext ? (loc, d) => ext.vertexAttribDivisorANGLE(loc, d) : () => {},
    drawInstanced: isTwo ? (mode, n, count) => gl.drawArraysInstanced(mode, 0, n, count) : ext ? (mode, n, count) => ext.drawArraysInstancedANGLE(mode, 0, n, count) : null,
    loc: {
      mesh: { aPos: at(pm, 'aPos'), aNormal: at(pm, 'aNormal'), aOff: at(pm, 'aOff'), aScl: at(pm, 'aScl'), aTint: at(pm, 'aTint'), uVP: un(pm, 'uVP'), uL: un(pm, 'uL'), uE: un(pm, 'uE') },
      line: { aPos: at(pl, 'aPos'), aTint: at(pl, 'aTint'), uVP: un(pl, 'uVP') },
      dot: { aPos: at(pd, 'aPos'), aTint: at(pd, 'aTint'), aCorner: at(pd, 'aCorner'), uVP: un(pd, 'uVP'), uPS: un(pd, 'uPS'), uView: un(pd, 'uView') },
      halo: { aPos: at(ph, 'aPos'), aTint: at(ph, 'aTint'), aSz: at(ph, 'aSz'), aCorner: at(ph, 'aCorner'), uVP: un(ph, 'uVP'), uK: un(ph, 'uK'), uT: un(ph, 'uT'), uView: un(ph, 'uView') },
    },
    info: { renderer, version: String(gl.getParameter(gl.VERSION)), software: GL_SOFTWARE.test(renderer), instancing },
  };
}

const GL_CORNERS = [[-1, -1], [1, -1], [1, 1], [-1, -1], [1, 1], [-1, 1]];
/** Halo sprites (9 floats each: position, colour, alpha, size, phase) as camera-facing quads: position 3, tint 4, size and phase 2, corner 2 per vertex. */
export function glHaloQuads(h, count) {
  const out = new Float32Array(count * 6 * 11); let o = 0;
  for (let i = 0; i < count; i++) {
    const k = i * 9;
    for (const c of GL_CORNERS) { for (let m = 0; m < 9; m++) out[o++] = h[k + m]; out[o++] = c[0]; out[o++] = c[1]; }
  }
  return out;
}
/** Event dots (7 floats each: position, colour, alpha) as quads: position 3, tint 4, corner 2 per vertex. */
export function glDotQuads(p, count) {
  const out = new Float32Array(count * 6 * 9); let o = 0;
  for (let i = 0; i < count; i++) {
    const k = i * 7;
    for (const c of GL_CORNERS) { for (let m = 0; m < 7; m++) out[o++] = p[k + m]; out[o++] = c[0]; out[o++] = c[1]; }
  }
  return out;
}

const glUpload = (rs, buffer, data, loc, size, stride = 0, offset = 0) => {
  const gl = rs.gl;
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
  gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, stride, offset);
};

/** Every attribute a pass may have left enabled or divided goes back to the plain state, so the next program starts clean. */
function glReset(rs) {
  const gl = rs.gl;
  for (let i = 0; i < 8; i++) { gl.disableVertexAttribArray(i); rs.divisor(i, 0); }
}

/**
 * Draw one frame. f: { buffers (makeBuffers result), groups (toInstanceBuffers result), vp (Float32Array 16), eye, width, height (pixels),
 * theme, clock (seconds) }. Returns the number of draw calls issued.
 */
export function glDraw(rs, f) {
  const gl = rs.gl; const L = rs.loc; const b = f.buffers; const th = GL_THEMES[f.theme] || GL_THEMES.dark;
  let calls = 0;
  gl.viewport(0, 0, f.width, f.height);
  gl.clearColor(th.bg[0], th.bg[1], th.bg[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.disable(gl.CULL_FACE);
  gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  glReset(rs);
  // parts: sorted far to near by the buffer builder and translucent, so they do not write depth
  gl.useProgram(rs.pm); gl.depthMask(false);
  gl.uniformMatrix4fv(L.mesh.uVP, false, f.vp); gl.uniform3f(L.mesh.uL, GL_LIGHT[0], GL_LIGHT[1], GL_LIGHT[2]); gl.uniform3fv(L.mesh.uE, f.eye);
  if (rs.drawInstanced && f.groups) {
    for (const g of f.groups) {
      if (!g.count) continue;
      glReset(rs);
      glUpload(rs, rs.buf.pos, g.template.pos, L.mesh.aPos, 3); glUpload(rs, rs.buf.nrm, g.template.nrm, L.mesh.aNormal, 3);
      glUpload(rs, rs.buf.off, g.offset, L.mesh.aOff, 3); rs.divisor(L.mesh.aOff, 1);
      glUpload(rs, rs.buf.scl, g.scale, L.mesh.aScl, 1); rs.divisor(L.mesh.aScl, 1);
      glUpload(rs, rs.buf.col, g.color, L.mesh.aTint, 4); rs.divisor(L.mesh.aTint, 1);
      rs.drawInstanced(gl.TRIANGLES, g.template.faceCount * 3, g.count); calls++;
    }
  } else if (b.mesh.faceCount) {
    glReset(rs);
    glUpload(rs, rs.buf.pos, b.mesh.pos, L.mesh.aPos, 3); glUpload(rs, rs.buf.nrm, b.mesh.nrm, L.mesh.aNormal, 3); glUpload(rs, rs.buf.col, b.mesh.col, L.mesh.aTint, 4);
    gl.vertexAttrib3f(L.mesh.aOff, 0, 0, 0); gl.vertexAttrib1f(L.mesh.aScl, 1);
    gl.drawArrays(gl.TRIANGLES, 0, b.mesh.faceCount * 3); calls++;
  }
  // edges, frames, state markers
  glReset(rs);
  gl.useProgram(rs.pl);
  gl.uniformMatrix4fv(L.line.uVP, false, f.vp);
  const stride = 7 * 4;
  if (b.lineCount) {
    glUpload(rs, rs.buf.line, b.lines, L.line.aPos, 3, stride, 0);
    gl.enableVertexAttribArray(L.line.aTint); gl.vertexAttribPointer(L.line.aTint, 4, gl.FLOAT, false, stride, 12);
    gl.drawArrays(gl.LINES, 0, b.lineCount); calls++;
  }
  // state halos: additive soft sprites around the parts that are executing
  if (b.haloCount) {
    glReset(rs);
    gl.useProgram(rs.ph); gl.blendFunc(gl.SRC_ALPHA, gl.ONE); gl.disable(gl.DEPTH_TEST);
    gl.uniformMatrix4fv(L.halo.uVP, false, f.vp); gl.uniform1f(L.halo.uK, f.vp[5] * f.height * 0.5); gl.uniform1f(L.halo.uT, f.clock); gl.uniform2f(L.halo.uView, f.width, f.height);
    const hs = 11 * 4;
    glUpload(rs, rs.buf.pt, glHaloQuads(b.halos, b.haloCount), L.halo.aPos, 3, hs, 0);
    gl.enableVertexAttribArray(L.halo.aTint); gl.vertexAttribPointer(L.halo.aTint, 4, gl.FLOAT, false, hs, 12);
    gl.enableVertexAttribArray(L.halo.aSz); gl.vertexAttribPointer(L.halo.aSz, 2, gl.FLOAT, false, hs, 28);
    gl.enableVertexAttribArray(L.halo.aCorner); gl.vertexAttribPointer(L.halo.aCorner, 2, gl.FLOAT, false, hs, 36);
    gl.drawArrays(gl.TRIANGLES, 0, b.haloCount * 6); calls++;
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.enable(gl.DEPTH_TEST);
  }
  // event energy moving along an edge (4D)
  if (b.dotCount) {
    glReset(rs);
    gl.useProgram(rs.pd); gl.disable(gl.DEPTH_TEST); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
    gl.uniformMatrix4fv(L.dot.uVP, false, f.vp); gl.uniform1f(L.dot.uPS, 13 + 3 * Math.sin(f.clock * 8)); gl.uniform2f(L.dot.uView, f.width, f.height);
    const ds = 9 * 4;
    glUpload(rs, rs.buf.line, glDotQuads(b.points, b.dotCount), L.dot.aPos, 3, ds, 0);
    gl.enableVertexAttribArray(L.dot.aTint); gl.vertexAttribPointer(L.dot.aTint, 4, gl.FLOAT, false, ds, 12);
    gl.enableVertexAttribArray(L.dot.aCorner); gl.vertexAttribPointer(L.dot.aCorner, 2, gl.FLOAT, false, ds, 28);
    gl.drawArrays(gl.TRIANGLES, 0, b.dotCount * 6); calls++;
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.enable(gl.DEPTH_TEST);
  }
  gl.depthMask(true);
  glReset(rs);
  return calls;
}
