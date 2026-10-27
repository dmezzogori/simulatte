import{w as d,g as x,c as s}from"./index-BQtptVuW.js";const p={name:"local-uniform-bit",vertex:{header:`

            struct LocalUniforms {
                uTransformMatrix:mat3x3<f32>,
                uColor:vec4<f32>,
                uRound:f32,
            }

            @group(1) @binding(0) var<uniform> localUniforms : LocalUniforms;
        `,main:`
            vColor *= localUniforms.uColor;
            modelMatrix *= localUniforms.uTransformMatrix;
        `,end:`
            if(localUniforms.uRound == 1)
            {
                vPosition = vec4(roundPixels(vPosition.xy, globalUniforms.uResolution), vPosition.zw);
            }
        `}},M={...p,vertex:{...p.vertex,header:p.vertex.header.replace("group(1)","group(2)")}},g={name:"local-uniform-bit",vertex:{header:`

            uniform mat3 uTransformMatrix;
            uniform vec4 uColor;
            uniform float uRound;
        `,main:`
            vColor *= uColor;
            modelMatrix = uTransformMatrix;
        `,end:`
            if(uRound == 1.)
            {
                gl_Position.xy = roundPixels(gl_Position.xy, uResolution);
            }
        `}},T={name:"texture-bit",vertex:{header:`

        struct TextureUniforms {
            uTextureMatrix:mat3x3<f32>,
        }

        @group(2) @binding(2) var<uniform> textureUniforms : TextureUniforms;
        `,main:`
            uv = (textureUniforms.uTextureMatrix * vec3(uv, 1.0)).xy;
        `},fragment:{header:`
            @group(2) @binding(0) var uTexture: texture_2d<f32>;
            @group(2) @binding(1) var uSampler: sampler;


        `,main:`
            outColor = textureSample(uTexture, uSampler, vUV);
        `}},R={name:"texture-bit",vertex:{header:`
            uniform mat3 uTextureMatrix;
        `,main:`
            uv = (uTextureMatrix * vec3(uv, 1.0)).xy;
        `},fragment:{header:`
        uniform sampler2D uTexture;


        `,main:`
            outColor = texture(uTexture, vUV);
        `}};function U(a,n){for(const t in a.attributes){const r=a.attributes[t],o=n[t];o?(r.format??(r.format=o.format),r.offset??(r.offset=o.offset),r.instance??(r.instance=o.instance)):r.format||d(`Attribute ${t} is not present in the shader, but is present in the geometry. Unable to infer attribute details.`)}b(a)}function b(a){const{buffers:n,attributes:t}=a,r={},o={};for(const i in n){const e=n[i];r[e.uid]=0,o[e.uid]=0}for(const i in t){const e=t[i];r[e.buffer.uid]+=x(e.format).stride}for(const i in t){const e=t[i];e.stride??(e.stride=r[e.buffer.uid]),e.start??(e.start=o[e.buffer.uid]),o[e.buffer.uid]+=x(e.format).stride}}const u=[];u[s.NONE]=void 0;u[s.DISABLED]={stencilWriteMask:0,stencilReadMask:0};u[s.RENDERING_MASK_ADD]={stencilFront:{compare:"equal",passOp:"increment-clamp"},stencilBack:{compare:"equal",passOp:"increment-clamp"}};u[s.RENDERING_MASK_REMOVE]={stencilFront:{compare:"equal",passOp:"decrement-clamp"},stencilBack:{compare:"equal",passOp:"decrement-clamp"}};u[s.MASK_ACTIVE]={stencilWriteMask:0,stencilFront:{compare:"equal",passOp:"keep"},stencilBack:{compare:"equal",passOp:"keep"}};u[s.INVERSE_MASK_ACTIVE]={stencilWriteMask:0,stencilFront:{compare:"not-equal",passOp:"keep"},stencilBack:{compare:"not-equal",passOp:"keep"}};function S(a,n,t,r,o){if(a=Math.max(0,a),n=Math.min(n,t*r),a>=n)return 0;const i=Math.floor(a/t),e=a-i*t,l=Math.floor(n/t),m=n-l*t;if(i===l)return o[0].set(e,i,m-e,1),1;let c=0,f=i;return e>0&&(o[c++].set(e,i,t-e,1),f++),l>f&&o[c++].set(0,f,t,l-f),m>0&&o[c++].set(0,l,m,1),c}export{u as G,p as a,g as b,R as c,U as e,S as g,M as l,T as t};
