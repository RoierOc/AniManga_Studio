/* Anime4K sobre WebGPU para el player web — mismos shaders CNN (bloc97) que el
 * usuario ya usa en mpv, portados a compute shaders (paquete anime4k-webgpu).
 *
 * Bucle propio en vez del render() del paquete: necesitamos parar/rearmar al
 * cambiar de tier, de episodio o de pista sin fugar el device ni el rVFC loop.
 *
 * Flujo por frame: vídeo → copyExternalImageToTexture → pipelines Anime4K
 * (compute) → blit a un <canvas> webgpu que se muestra ENCIMA del <video>
 * (el vídeo sigue reproduciendo oculto: audio, subs y timing intactos). */
// Import dinámico: los pesos CNN pesan ~4 MB — solo se descargan la primera
// vez que se activa un tier, no en la carga de la app.
export const A4K_MODES = [
  { id: 'off', label: 'Desactivado' },
  { id: 'A',   label: 'A — Rápido (restaura + x2)' },
  { id: 'B',   label: 'B — Rápido (suave)' },
  { id: 'C',   label: 'C — Rápido (denoise)' },
  { id: 'AA',  label: 'A+A — Alta calidad' },
  { id: 'BB',  label: 'B+B — Alta calidad' },
  { id: 'CA',  label: 'C+A — Alta calidad' },
]
let _mod = null
async function presets() {
  if (!_mod) _mod = await import('anime4k-webgpu')
  const m = _mod
  return { A: m.ModeA, B: m.ModeB, C: m.ModeC, AA: m.ModeAA, BB: m.ModeBB, CA: m.ModeCA }
}

const BLIT_WGSL = /* wgsl */ `
@vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4f {
  var pos = array(vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0));
  return vec4f(pos[i], 0.0, 1.0);
}
@group(0) @binding(0) var t: texture_2d<f32>;
@group(0) @binding(1) var s: sampler;
@fragment fn fs(@builtin(position) p: vec4f) -> @location(0) vec4f {
  let dims = vec2f(textureDimensions(t));
  return textureSample(t, s, p.xy / dims);
}`

export class Anime4KRenderer {
  constructor() {
    this.device = null
    this.running = false
    this._vfcHandle = 0
  }

  static supported() { return !!navigator.gpu }

  /** Arranca (o re-arma) el pipeline para video→canvas con el tier dado. */
  async start(video, canvas, modeId) {
    this.stop()
    if (!navigator.gpu || !video.videoWidth) return false
    const Preset = (await presets())[modeId]
    if (!Preset) return false

    if (!this.device) {
      // low-power = la iGPU Intel, la MISMA que decodifica el vídeo (VAAPI):
      // los VideoFrame se importan zero-copy. Con high-performance (NVIDIA)
      // el import cross-GPU congela el decoder (vídeo clavado, canvas negro).
      const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'low-power' })
        || await navigator.gpu.requestAdapter()
      if (!adapter) return false
      this.device = await adapter.requestDevice()
      this.device.lost.then(() => { this.device = null; this.stop() })
    }
    const device = this.device
    const vw = video.videoWidth, vh = video.videoHeight
    const tw = vw * 2, th = vh * 2               // Anime4K = upscale x2

    canvas.width = tw
    canvas.height = th
    const ctx = canvas.getContext('webgpu')
    const format = navigator.gpu.getPreferredCanvasFormat()
    ctx.configure({ device, format, alphaMode: 'opaque' })

    const inputTexture = device.createTexture({
      size: [vw, vh],
      format: 'rgba8unorm',
      usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST
           | GPUTextureUsage.RENDER_ATTACHMENT,
    })
    const pipeline = new Preset({
      device, inputTexture,
      nativeDimensions: { width: vw, height: vh },
      targetDimensions: { width: tw, height: th },
    })

    const blitModule = device.createShaderModule({ code: BLIT_WGSL })
    const blit = device.createRenderPipeline({
      layout: 'auto',
      vertex: { module: blitModule, entryPoint: 'vs' },
      fragment: { module: blitModule, entryPoint: 'fs', targets: [{ format }] },
    })
    const sampler = device.createSampler({ magFilter: 'linear', minFilter: 'linear' })
    const bindGroup = device.createBindGroup({
      layout: blit.getBindGroupLayout(0),
      entries: [
        { binding: 0, resource: pipeline.getOutputTexture().createView() },
        { binding: 1, resource: sampler },
      ],
    })

    this.running = true
    let errCount = 0
    const frame = () => {
      if (!this.running) return
      try {
        // copyExternalImageToTexture NO acepta HTMLVideoElement (spec): hay que
        // envolver el frame actual en un VideoFrame (WebCodecs, zero-copy).
        const vf = new VideoFrame(video)
        try {
          device.queue.copyExternalImageToTexture(
            { source: vf }, { texture: inputTexture }, [vw, vh])
        } finally {
          vf.close()
        }
        const encoder = device.createCommandEncoder()
        pipeline.pass(encoder)
        const rp = encoder.beginRenderPass({
          colorAttachments: [{
            view: ctx.getCurrentTexture().createView(),
            loadOp: 'clear', storeOp: 'store', clearValue: { r: 0, g: 0, b: 0, a: 1 },
          }],
        })
        rp.setPipeline(blit)
        rp.setBindGroup(0, bindGroup)
        rp.draw(3)
        rp.end()
        device.queue.submit([encoder.finish()])
        errCount = 0
      } catch (e) {
        // frames sueltos pueden fallar en seeks; errores SOSTENIDOS = pipeline
        // rota → apagar el shader y avisar (nunca dejar el canvas en negro)
        if (++errCount === 1) console.warn('[a4k] error de frame:', e)
        if (errCount > 30) {
          console.error('[a4k] errores sostenidos — apagando shader:', e)
          this.stop()
          this.onFatal?.(e)
          return
        }
      }
      this._vfcHandle = video.requestVideoFrameCallback(frame)
    }
    this._video = video
    this._vfcHandle = video.requestVideoFrameCallback(frame)
    return true
  }

  stop() {
    this.running = false
    if (this._video && this._vfcHandle) {
      try { this._video.cancelVideoFrameCallback(this._vfcHandle) } catch (_) {}
    }
    this._vfcHandle = 0
  }

  destroy() {
    this.stop()
    try { this.device?.destroy() } catch (_) {}
    this.device = null
  }
}
