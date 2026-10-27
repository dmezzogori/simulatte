import{M as w,d as B,Q as O,ay as A,ae as k,E as _,G as M,b as E,O as U,H as z,af as F,T as v,_ as V,R as q,w as L,o as P}from"./index-BQtptVuW.js";import{F as W}from"./Filter-CxgSmKQZ.js";const b=new w;function G(g,e){e.clear();const t=e.matrix;for(let r=0;r<g.length;r++){const i=g[r];if(i.globalDisplayStatus<7)continue;const o=i.renderGroup??i.parentRenderGroup;o?.isCachedAsTexture?e.matrix=b.copyFrom(o.textureOffsetInverseTransform).append(i.worldTransform):o?._parentCacheAsTextureRenderGroup?e.matrix=b.copyFrom(o._parentCacheAsTextureRenderGroup.inverseWorldTransform).append(i.groupTransform):e.matrix=i.worldTransform,e.addBounds(i.bounds)}return e.matrix=t,e}function y(g){return typeof g.getCanvasFilterString=="function"}class Y{constructor(){this.skip=!1,this.useClip=!1,this.filters=null,this.container=null,this.bounds=new k,this.cssFilterString=""}}class R{constructor(e){this._filterStack=[],this._filterStackIndex=0,this._savedStates=[],this._alphaMultiplier=1,this._warnedFilterTypes=new Set,this.renderer=e}push(e){const t=this._pushFilterFrame(),r=e.filterEffect.filters;if(t.skip=!1,t.useClip=!1,t.filters=r,t.container=e.container,t.cssFilterString="",r.every(s=>!s.enabled)){t.skip=!0;return}const i=[],o=1;for(const s of r){if(!s.enabled)continue;if(!y(s)){this._warnUnsupportedFilter(s);continue}const a=s.getCanvasFilterString();if(a===null){this._warnUnsupportedFilter(s);continue}a&&i.push(a)}if(i.length===0&&o===1){t.skip=!0;return}t.cssFilterString=i.join(" "),this._calculateFilterArea(e,t.bounds),t.useClip=!!e.filterEffect.filterArea;const n=this.renderer.canvasContext.activeContext,l=n.filter||"none";if(this._savedStates.push({filter:l,alphaMultiplier:this._alphaMultiplier}),t.useClip&&Number.isFinite(t.bounds.width)&&Number.isFinite(t.bounds.height)&&t.bounds.width>0&&t.bounds.height>0){const s=this.renderer.canvasContext.activeResolution||1;n.save(),n.setTransform(1,0,0,1,0,0),n.beginPath(),n.rect(t.bounds.x*s,t.bounds.y*s,t.bounds.width*s,t.bounds.height*s),n.clip()}else t.useClip=!1;t.cssFilterString&&(n.filter=l!=="none"?`${l} ${t.cssFilterString}`:t.cssFilterString)}pop(){const e=this._popFilterFrame();if(e.skip)return;const t=this._savedStates.pop();if(!t)return;const r=this.renderer.canvasContext.activeContext;e.useClip?r.restore():r.filter=t.filter,this._alphaMultiplier=t.alphaMultiplier}generateFilteredTexture({texture:e,filters:t}){if(!t?.length||t.every(p=>!p.enabled))return e;const r=[],i=1;for(const p of t){if(!p.enabled)continue;if(!y(p)){this._warnUnsupportedFilter(p);continue}const h=p.getCanvasFilterString();if(h===null){this._warnUnsupportedFilter(p);continue}h&&r.push(h)}if(r.length===0&&i===1)return e;const o=B.getCanvasSource(e);if(!o)return e;const n=e.frame,l=e.source._resolution??e.source.resolution??1,s=n.width,a=n.height,f=O.getOptimalCanvasAndContext(s,a,l),{canvas:c,context:u}=f;u.setTransform(1,0,0,1,0,0),u.clearRect(0,0,c.width,c.height),r.length&&(u.filter=r.join(" "));const d=n.x*l,m=n.y*l,x=s*l,T=a*l;return u.drawImage(o,d,m,x,T,0,0,x,T),u.filter="none",u.globalAlpha=1,A(c,s,a,l)}_calculateFilterArea(e,t){if(e.renderables?G(e.renderables,t):e.filterEffect.filterArea?(t.clear(),t.addRect(e.filterEffect.filterArea),t.applyMatrix(e.container.worldTransform)):e.container.getFastGlobalBounds(!0,t),e.container){const i=(e.container.renderGroup||e.container.parentRenderGroup)?.cacheToLocalTransform;i&&t.applyMatrix(i)}}_warnUnsupportedFilter(e){const t=e?.constructor?.name||"Filter";this._warnedFilterTypes.has(t)||(this._warnedFilterTypes.add(t),console.warn(`CanvasRenderer: filter "${t}" is not supported in Canvas2D and will be skipped.`))}get alphaMultiplier(){return this._alphaMultiplier}_pushFilterFrame(){let e=this._filterStack[this._filterStackIndex];return e||(e=this._filterStack[this._filterStackIndex]=new Y),this._filterStackIndex++,e}_popFilterFrame(){return this._filterStackIndex<=0?this._filterStack[0]:(this._filterStackIndex--,this._filterStack[this._filterStackIndex])}destroy(){this._filterStack=null,this._savedStates=null,this._warnedFilterTypes=null,this._alphaMultiplier=1}}R.extension={type:[_.CanvasSystem],name:"filter"};var D=`in vec2 aPosition;
out vec2 vTextureCoord;

uniform vec4 uInputSize;
uniform vec4 uOutputFrame;
uniform vec4 uOutputTexture;

vec4 filterVertexPosition( void )
{
    vec2 position = aPosition * uOutputFrame.zw + uOutputFrame.xy;
    
    position.x = position.x * (2.0 / uOutputTexture.x) - 1.0;
    position.y = position.y * (2.0*uOutputTexture.z / uOutputTexture.y) - uOutputTexture.z;

    return vec4(position, 0.0, 1.0);
}

vec2 filterTextureCoord( void )
{
    return aPosition * (uOutputFrame.zw * uInputSize.zw);
}

void main(void)
{
    gl_Position = filterVertexPosition();
    vTextureCoord = filterTextureCoord();
}
`,X=`in vec2 vTextureCoord;
out vec4 finalColor;
uniform sampler2D uTexture;
void main() {
    finalColor = texture(uTexture, vTextureCoord);
}
`,S=`struct GlobalFilterUniforms {
  uInputSize: vec4<f32>,
  uInputPixel: vec4<f32>,
  uInputClamp: vec4<f32>,
  uOutputFrame: vec4<f32>,
  uGlobalFrame: vec4<f32>,
  uOutputTexture: vec4<f32>,
};

@group(0) @binding(0) var <uniform> gfu: GlobalFilterUniforms;
@group(0) @binding(1) var uTexture: texture_2d<f32>;
@group(0) @binding(2) var uSampler: sampler;

struct VSOutput {
  @builtin(position) position: vec4<f32>,
  @location(0) uv: vec2<f32>
};

fn filterVertexPosition(aPosition: vec2<f32>) -> vec4<f32>
{
    var position = aPosition * gfu.uOutputFrame.zw + gfu.uOutputFrame.xy;

    position.x = position.x * (2.0 / gfu.uOutputTexture.x) - 1.0;
    position.y = position.y * (2.0 * gfu.uOutputTexture.z / gfu.uOutputTexture.y) - gfu.uOutputTexture.z;

    return vec4(position, 0.0, 1.0);
}

fn filterTextureCoord(aPosition: vec2<f32>) -> vec2<f32>
{
    return aPosition * (gfu.uOutputFrame.zw * gfu.uInputSize.zw);
}

@vertex
fn mainVertex(
  @location(0) aPosition: vec2<f32>,
) -> VSOutput {
  return VSOutput(
   filterVertexPosition(aPosition),
   filterTextureCoord(aPosition)
  );
}

@fragment
fn mainFragment(
  @location(0) uv: vec2<f32>,
) -> @location(0) vec4<f32> {
    return textureSample(uTexture, uSampler, uv);
}
`;class j extends W{constructor(){const e=M.from({vertex:{source:S,entryPoint:"mainVertex"},fragment:{source:S,entryPoint:"mainFragment"},name:"passthrough-filter"}),t=E.from({vertex:D,fragment:X,name:"passthrough-filter"});super({gpuProgram:e,glProgram:t})}}class C{constructor(e){this._renderer=e}push(e,t,r){this._renderer.renderPipes.batch.break(r),r.add({renderPipeId:"filter",canBundle:!1,action:"pushFilter",container:t,filterEffect:e})}pop(e,t,r){this._renderer.renderPipes.batch.break(r),r.add({renderPipeId:"filter",action:"popFilter",canBundle:!1})}execute(e){e.action==="pushFilter"?this._renderer.filter.push(e):e.action==="popFilter"&&this._renderer.filter.pop()}destroy(){this._renderer=null}}C.extension={type:[_.WebGLPipes,_.WebGPUPipes,_.CanvasPipes],name:"filter"};const N=new V({attributes:{aPosition:{buffer:new Float32Array([0,0,1,0,1,1,0,1]),format:"float32x2",stride:8,offset:0}},indexBuffer:new Uint32Array([0,1,2,0,2,3])});class ${constructor(){this.skip=!1,this.inputTexture=null,this.backTexture=null,this.filters=null,this.bounds=new k,this.container=null,this.blendRequired=!1,this.outputRenderSurface=null,this.firstEnabledIndex=-1,this.lastEnabledIndex=-1}}class I{constructor(e){this._filterStackIndex=0,this._filterStack=[],this._filterGlobalUniforms=new U({uInputSize:{value:new Float32Array(4),type:"vec4<f32>"},uInputPixel:{value:new Float32Array(4),type:"vec4<f32>"},uInputClamp:{value:new Float32Array(4),type:"vec4<f32>"},uOutputFrame:{value:new Float32Array(4),type:"vec4<f32>"},uGlobalFrame:{value:new Float32Array(4),type:"vec4<f32>"},uOutputTexture:{value:new Float32Array(4),type:"vec4<f32>"}}),this._globalFilterBindGroup=new z({}),this.renderer=e}get activeBackTexture(){return this._activeFilterData?.backTexture}push(e){const t=this.renderer,r=e.filterEffect.filters,i=this._pushFilterData();i.skip=!1,i.filters=r,i.container=e.container,i.outputRenderSurface=t.renderTarget.renderSurface;const o=t.renderTarget.renderTarget.colorTexture.source,n=o.resolution,l=o.antialias;if(r.every(f=>!f.enabled)){i.skip=!0;return}const s=i.bounds;if(this._calculateFilterArea(e,s),this._calculateFilterBounds(i,t.renderTarget.rootViewPort,l,n,1),i.skip)return;const a=this._getPreviousFilterData();this._setupFilterTextures(i,s,t,a)}generateFilteredTexture({texture:e,filters:t}){if(t.every(f=>!f.enabled))return e;const r=this._pushFilterData();this._activeFilterData=r,r.skip=!1,r.filters=t;const i=e.source,o=i.resolution,n=i.antialias,l=r.bounds;if(l.clear(),l.addRect(e.frame),this._calculateFilterBounds(r,l.rectangle,n,o,0),r.skip)return this._popFilterData(),e;r.outputRenderSurface=F.getOptimalTexture({width:l.width,height:l.height,resolution:r.resolution,antialias:r.antialias}),r.backTexture=v.EMPTY,r.inputTexture=e,this.renderer.renderTarget.finishRenderPass(),this._applyFiltersToTexture(r,!0);const a=r.outputRenderSurface;return a.source.alphaMode="premultiplied-alpha",this._popFilterData(),a}pop(){const e=this.renderer,t=this._popFilterData();t.skip||(e.globalUniforms.pop(),e.renderTarget.finishRenderPass(),this._activeFilterData=t,this._applyFiltersToTexture(t,!1),t.blendRequired&&F.returnTexture(t.backTexture),F.returnTexture(t.inputTexture))}getBackTexture(e,t,r){const i=e.colorTexture.source._resolution,o=F.getOptimalTexture({width:t.width,height:t.height,resolution:i});let n=t.minX,l=t.minY;r&&(n-=r.minX,l-=r.minY),n=Math.floor(n*i),l=Math.floor(l*i);const s=Math.ceil(t.width*i),a=Math.ceil(t.height*i);return this.renderer.renderTarget.copyToTexture(e,o,{x:n,y:l},{width:s,height:a},{x:0,y:0}),o}applyFilter(e,t,r,i){const o=this.renderer,n=this._activeFilterData,s=n.outputRenderSurface===r,a=this._findClosestFilterData(),f=a?a.inputTexture.source._resolution:o.renderTarget.rootRenderTarget.colorTexture.source._resolution;let c=0,u=0;s&&a&&(c=a.bounds.minX,u=a.bounds.minY),this._updateFilterUniforms(t,r,n,c,u,f,s,i);const d=e.enabled?e:this._getPassthroughFilter();this._setupBindGroupsAndRender(d,t,o)}calculateSpriteMatrix(e,t){const r=this._activeFilterData,i=e.set(r.inputTexture._source.width,0,0,r.inputTexture._source.height,r.bounds.minX,r.bounds.minY),o=t.worldTransform.copyTo(w.shared),n=t.renderGroup||t.parentRenderGroup;return n&&n.cacheToLocalTransform&&o.prepend(n.cacheToLocalTransform),o.invert(),i.prepend(o),i.scale(1/t.texture.orig.width,1/t.texture.orig.height),i.translate(t.anchor.x,t.anchor.y),i}destroy(){this._passthroughFilter?.destroy(!0),this._passthroughFilter=null}_getPassthroughFilter(){return this._passthroughFilter??(this._passthroughFilter=new j),this._passthroughFilter}_setupBindGroupsAndRender(e,t,r){if(r.renderPipes.uniformBatch){const i=r.renderPipes.uniformBatch.getUboResource(this._filterGlobalUniforms);this._globalFilterBindGroup.setResource(i,0)}else this._globalFilterBindGroup.setResource(this._filterGlobalUniforms,0);this._globalFilterBindGroup.setResource(t.source,1),this._globalFilterBindGroup.setResource(t.source.style,2),e.groups[0]=this._globalFilterBindGroup,r.encoder.draw({geometry:N,shader:e,state:e._state,topology:"triangle-list"}),r.type===q.WEBGL&&r.renderTarget.finishRenderPass()}_setupFilterTextures(e,t,r,i){if(e.backTexture=v.EMPTY,e.inputTexture=F.getOptimalTexture({width:t.width,height:t.height,resolution:e.resolution,antialias:e.antialias}),e.blendRequired){r.renderTarget.finishRenderPass();const o=r.renderTarget.getRenderTarget(e.outputRenderSurface);e.backTexture=this.getBackTexture(o,t,i?.bounds)}r.renderTarget.bind({target:e.inputTexture,clear:!0}),r.globalUniforms.push({offset:t})}_updateFilterUniforms(e,t,r,i,o,n,l,s){const a=this._filterGlobalUniforms.uniforms,f=a.uOutputFrame,c=a.uInputSize,u=a.uInputPixel,d=a.uInputClamp,m=a.uGlobalFrame,x=a.uOutputTexture;l?(f[0]=r.bounds.minX-i,f[1]=r.bounds.minY-o):(f[0]=0,f[1]=0),f[2]=e.frame.width,f[3]=e.frame.height,c[0]=e.source.width,c[1]=e.source.height,c[2]=1/c[0],c[3]=1/c[1],u[0]=e.source.pixelWidth,u[1]=e.source.pixelHeight,u[2]=1/u[0],u[3]=1/u[1],d[0]=.5*u[2],d[1]=.5*u[3],d[2]=e.frame.width*c[2]-.5*u[2],d[3]=e.frame.height*c[3]-.5*u[3];const T=this.renderer.renderTarget.rootRenderTarget.colorTexture;m[0]=i*n,m[1]=o*n,m[2]=T.source.width*n,m[3]=T.source.height*n,t instanceof v&&(t.source.resource=null);const p=this.renderer.renderTarget.getRenderTarget(t);this.renderer.renderTarget.bind({target:t,clear:!!s}),t instanceof v?(x[0]=t.frame.width,x[1]=t.frame.height):(x[0]=p.width,x[1]=p.height),x[2]=p.isRoot?-1:1,this._filterGlobalUniforms.update()}_findClosestFilterData(){for(let e=this._filterStackIndex-1;e>=0;e--){const t=this._filterStack[e];if(!t.skip)return t}return null}_calculateFilterArea(e,t){if(e.renderables?G(e.renderables,t):e.filterEffect.filterArea?(t.clear(),t.addRect(e.filterEffect.filterArea),t.applyMatrix(e.container.worldTransform)):e.container.getFastGlobalBounds(!0,t),e.container){const i=(e.container.renderGroup||e.container.parentRenderGroup).cacheToLocalTransform;i&&t.applyMatrix(i)}}_applyFiltersToTexture(e,t){const r=e.inputTexture,i=e.bounds,o=e.filters,n=e.firstEnabledIndex,l=e.lastEnabledIndex;if(this._globalFilterBindGroup.setResource(r.source.style,2),this._globalFilterBindGroup.setResource(e.backTexture.source,3),n===l)o[n].apply(this,r,e.outputRenderSurface,t);else{let s=e.inputTexture;const a=F.getOptimalTexture({width:i.width,height:i.height,resolution:s.source._resolution});let f=a;for(let c=n;c<l;c++){const u=o[c];if(!u.enabled)continue;u.apply(this,s,f,!0);const d=s;s=f,f=d}o[l].apply(this,s,e.outputRenderSurface,t),F.returnTexture(a)}}_calculateFilterBounds(e,t,r,i,o){const n=this.renderer,l=e.bounds,s=e.filters;let a=1/0,f=0,c=!0,u=!1,d=!1,m=!0,x=-1,T=-1;for(let p=0;p<s.length;p++){const h=s[p];if(!h.enabled)continue;if(x===-1&&(x=p),T=p,a=Math.min(a,h.resolution==="inherit"?i:h.resolution),f+=h.padding,h.antialias==="off"?c=!1:h.antialias==="inherit"&&c&&(c=r),h.clipToViewport||(m=!1),!!!(h.compatibleRenderers&n.type)){d=!1;break}if(h.blendRequired&&!(n.backBuffer?.useBackBuffer??!0)){L("Blend filter requires backBuffer on WebGL renderer to be enabled. Set `useBackBuffer: true` in the renderer options."),d=!1;break}d=!0,u||(u=h.blendRequired)}if(!d){e.skip=!0;return}if(m&&l.fitBounds(0,t.width/i,0,t.height/i),l.scale(a).ceil().scale(1/a).pad((f|0)*o),!l.isPositive){e.skip=!0;return}e.antialias=c,e.resolution=a,e.blendRequired=u,e.firstEnabledIndex=x,e.lastEnabledIndex=T}_popFilterData(){return this._filterStackIndex--,this._filterStack[this._filterStackIndex]}_getPreviousFilterData(){for(let e=this._filterStackIndex-2;e>=0;e--){const t=this._filterStack[e];if(!t.skip)return t}return null}_pushFilterData(){let e=this._filterStack[this._filterStackIndex];return e||(e=this._filterStack[this._filterStackIndex]=new $),this._filterStackIndex++,e}}I.extension={type:[_.WebGLSystem,_.WebGPUSystem],name:"filter"};P.add(I,R);P.add(C);
