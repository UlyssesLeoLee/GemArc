/** An interactive orientation gizmo drawn in real 3D.
 *
 *  Why in-scene rather than a 2D overlay: a flat compass lies the moment the user
 *  orbits. Here the axis triads are real geometry, so the X ball always sits where
 *  the X axis of the scene actually points. The reader builds a genuine mental model
 *  of the orientation instead of decoding a static sprite.
 *
 *  The gizmo is drawn in a SEPARATE scene rendered on top with its own camera, so it
 *  is never clipped by the main scene's near/far planes and its apparent size stays
 *  constant regardless of how far the reader has zoomed out.
 *
 *  Handedness: the viewer uses the Three.js default right-handed frame (+X right,
 *  +Y up, +Z toward the viewer). The gizmo draws the SAME frame, and says so, so a
 *  left-handed export target is an explicit, visible choice rather than a silent one.
 */
import * as THREE from 'three';
import {AXIS_COLORS} from './layout.js';
import {gizmoLabels} from './gizmo-core.js';

const AXES=[
  {key:'x',dir:[1,0,0],positive:[1,0,0],negative:[-1,0,0]},
  {key:'y',dir:[0,1,0],positive:[0,1,0],negative:[0,-1,0]},
  {key:'z',dir:[0,0,1],positive:[0,0,1],negative:[0,0,-1]},
];

export class AxisGizmo{
  /**
   * @param {object} opts
   * @param {HTMLElement} opts.host      element the gizmo canvas is appended to
   * @param {number} opts.size           pixel diameter of the gizmo viewport
   * @param {string} opts.lang           UI language, for the labels
   * @param {(axis:'x'|'y'|'z', positive:boolean)=>void} [opts.onPick]
   *        called when the reader clicks an axis end, so the host can align the camera
   *        to that axis -- the gizmo is a navigation control, not just a legend.
   */
  constructor({host,size=104,lang='zh-CN',onPick=null}={}){
    this.host=host;
    this.size=size;
    this.lang=lang;
    this.onPick=onPick;
    this.enabled=true;
    this.disposed=false;

    this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
    this.renderer.setSize(size,size);
    this.renderer.domElement.style.width=`${size}px`;
    this.renderer.domElement.style.height=`${size}px`;
    this.renderer.domElement.style.display='block';
    this.renderer.domElement.style.cursor='pointer';
    this.renderer.domElement.setAttribute('role','img');
    const lab=gizmoLabels(lang);
    this.renderer.domElement.setAttribute('aria-label',
      `${lab.hand} ${lab.x} ${lab.y} ${lab.z}`);
    this.renderer.domElement.addEventListener('pointerdown',e=>this.handleClick(e));
    if(host)host.append(this.renderer.domElement);

    this.scene=new THREE.Scene();
    // A perspective camera with a narrow FOV gives the gizmo its familiar "ball"
    // foreshortening without needing an actual skybox.
    this.camera=new THREE.PerspectiveCamera(75,1,.1,20);
    this.camera.position.set(0,0,3.1);

    this.scene.add(new THREE.AmbientLight(0xffffff,1.4));
    const key=new THREE.DirectionalLight(0xffffff,1.9);
    key.position.set(2,4,3);this.scene.add(key);

    this.axesGroup=new THREE.Group();
    this.scene.add(this.axesGroup);
    this.hitTargets=[];
    this.buildAxes();
  }

  buildAxes(){
    const R=1;
    const ballGeo=new THREE.SphereGeometry(.13,18,14);
    for(const a of AXES){
      const color=AXIS_COLORS[a.key];
      // Positive end gets a solid ball, negative end a hollow ring: the reader can
      // tell direction at a glance without reading a label.
      const posBall=new THREE.Mesh(ballGeo,new THREE.MeshBasicMaterial({color}));
      posBall.position.set(...a.positive.map(v=>v*R));
      posBall.userData={axis:a.key,positive:true};
      this.axesGroup.add(posBall);this.hitTargets.push(posBall);

      const negBall=new THREE.Mesh(ballGeo,new THREE.MeshBasicMaterial({
        color,transparent:true,opacity:.34,wireframe:true}));
      negBall.position.set(...a.negative.map(v=>v*R));
      negBall.userData={axis:a.key,positive:false};
      this.axesGroup.add(negBall);this.hitTargets.push(negBall);

      const dir=new THREE.Vector3(...a.dir).normalize();
      for(const sign of [1,-1]){
        const shaft=new THREE.Mesh(
          new THREE.CylinderGeometry(.014,.014,sign>0?R:.5,10),
          new THREE.MeshBasicMaterial({color,transparent:true,opacity:sign>0?.95:.4}));
        shaft.position.copy(dir).multiplyScalar(sign>0?R/2:R*.25);
        // Point the cylinder along the axis it represents.
        shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),dir.clone().multiplyScalar(sign));
        shaft.userData={axis:a.key,positive:sign>0};
        this.axesGroup.add(shaft);this.hitTargets.push(shaft);
      }
    }
  }

  /** Mirror the MAIN camera's orientation into the gizmo. */
  syncFrom(mainCamera){
    if(this.disposed)return;
    this.axesGroup.quaternion.copy(mainCamera.quaternion).invert();
  }

  render(){
    if(this.disposed||!this.enabled)return;
    this.renderer.render(this.scene,this.camera);
  }

  /** Click an axis end -> ask the host to align the view to that axis. */
  handleClick(event){
    if(!this.onPick)return;
    const rect=this.renderer.domElement.getBoundingClientRect();
    const ndc=new THREE.Vector2(
      ((event.clientX-rect.left)/rect.width)*2-1,
      -((event.clientY-rect.top)/rect.height)*2+1);
    const ray=new THREE.Raycaster();
    ray.setFromCamera(ndc,this.camera);
    const hit=ray.intersectObjects(this.hitTargets,false)[0];
    if(hit&&hit.object.userData.axis){
      this.onPick(hit.object.userData.axis,hit.object.userData.positive);
    }
  }

  setEnabled(on){
    this.enabled=!!on;
    this.renderer.domElement.style.opacity=on?'1':'.25';
    this.renderer.domElement.setAttribute('aria-hidden',on?'false':'true');
  }

  setLang(lang){this.lang=lang;const lab=gizmoLabels(lang);
    this.renderer.domElement.setAttribute('aria-label',`${lab.hand} ${lab.x} ${lab.y} ${lab.z}`);}

  dispose(){
    if(this.disposed)return;
    this.disposed=true;
    this.scene.traverse(o=>{
      if(o.geometry)o.geometry.dispose();
      if(o.material)(Array.isArray(o.material)?o.material:[o.material]).forEach(m=>m.dispose());
    });
    this.renderer.dispose();
    if(this.renderer.domElement.parentNode)this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
  }
}
