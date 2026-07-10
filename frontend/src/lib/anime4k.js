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
  { id: 'off',  label: 'Desactivado' },
  { id: 'LITE', label: 'Ligero — Deblur DoG (ideal iGPU)' },
  { id: 'A',    label: 'A — CNN (restaura + x2)' },
  { id: 'B',    label: 'B — CNN (suave)' },
  { id: 'C',    label: 'C — CNN (denoise)' },
  { id: 'AA',   label: 'A+A — CNN alta calidad' },
  { id: 'BB',   label: 'B+B — CNN alta calidad' },
  { id: 'CA',   label: 'C+A — CNN alta calidad' },
]
let _mod = null
async function presets() {
  if (!_mod) _mod = await import('anime4k-webgpu')
  const m = _mod
  return { LITE: m.DoG, A: m.ModeA, B: m.ModeB, C: m.ModeC, AA: m.ModeAA, BB: m.ModeBB, CA: m.ModeCA }
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

// Pre-pase: textura EXTERNA del vídeo (importExternalTexture, la vía del
// compositor — la única que puede leer frames decodificados por hardware/VAAPI)
// → textura rgba8 normal que las pipelines de Anime4K sí aceptan.
const INGEST_WGSL = /* wgsl */ `
@vertex fn vs(@builtin(vertex_index) i: u32) -> @builtin(position) vec4f {
  var pos = array(vec2f(-1.0, -1.0), vec2f(3.0, -1.0), vec2f(-1.0, 3.0));
  return vec4f(pos[i], 0.0, 1.0);
}
@group(0) @binding(0) var t: texture_external;
@group(0) @binding(1) var s: sampler;
@group(0) @binding(2) var<uniform> dims: vec2f;
@fragment fn fs(@builtin(position) p: vec4f) -> @location(0) vec4f {
  return textureSampleBaseClampToEdge(t, s, p.xy / dims);
}`

export class Anime4KRenderer {
  constructor() {
    this.device = null
    this.running = false
    this._vfcHandle = 0
  }

  static supported() { return !!navigator.gpu }

  /** Arranca (o re-arma) el pipeline para video→canvas con el tier dado.
   * targetW/H: resolución REAL de salida (rect visible × devicePixelRatio) —
   * computar a 2× nativo fijo (4K) desperdicia ~4× de GPU cuando el canvas se
   * muestra más pequeño; en la iGPU eso es la diferencia entre fluido y tirones. */
  async start(video, canvas, modeId, targetW = 0, targetH = 0) {
    this.stop()
    if (!navigator.gpu || !video.videoWidth) return false
    const Preset = (await presets())[modeId]
    if (!Preset) return false

    const vw0 = video.videoWidth, vh0 = video.videoHeight
    const tw0 = Math.round(Math.min(Math.max(targetW || vw0 * 2, vw0), vw0 * 2))
    const th0 = Math.round(Math.min(Math.max(targetH || vh0 * 2, vh0), vh0 * 2))
    const cacheKey = `${modeId}|${vw0}x${vh0}|${tw0}x${th0}`
    // Recompilar los compute pipelines (shaders CNN) tarda de verdad en algunas
    // GPU/driver — se notaba como "se queda cargando" al cambiar de episodio o
    // poner pantalla completa aunque el tier/resolución no hubieran cambiado.
    // Reusar el pipeline ya compilado cuando nada relevante cambió (mismo tier,
    // misma resolución nativa y destino, mismo canvas) evita ese recompute.
    if (this._cacheKey === cacheKey && this._pipeline && this._canvasEl === canvas && this.device) {
      this.running = true
      this._video = video
      this._runFrame()
      return true
    }

    if (!this.device) {
      // Linux híbrido: low-power = la iGPU que decodifica el vídeo (VAAPI). La
      // dedicada computa más rápido pero SIEMPRE produce negro: los frames no
      // cruzan de GPU por ninguna vía. En Windows el decode va por D3D11 en la
      // GPU principal → high-performance (la dedicada, p.ej. 5070 junto a la
      // iGPU del Ryzen). Override manual: 'anime-a4k-gpu'.
      const pref = localStorage.getItem('anime-a4k-gpu')
        || (navigator.platform.includes('Linux') ? 'low-power' : 'high-performance')
      const adapter = await navigator.gpu.requestAdapter({ powerPreference: pref })
        || await navigator.gpu.requestAdapter()
      if (!adapter) return false
      this.device = await adapter.requestDevice()
      this.device.lost.then((info) => {
        console.warn('[a4k] device WebGPU perdido:', info?.message)
        this.device = null
        this.stop()
        this.onFatal?.(info)   // canvas fuera, vídeo visible
      })
    }
    const device = this.device
    const vw = vw0, vh = vh0, tw = tw0, th = th0
    this.frames = 0                              // contador para diagnóstico

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

    // Cachear: reabrir el mismo tier a la misma resolución (próximo episodio,
    // reanudar) reusa esto en vez de recompilar los compute pipelines.
    this._cacheKey = cacheKey
    this._canvasEl = canvas
    this._ctx = ctx
    this._inputTexture = inputTexture
    this._pipeline = pipeline
    this._blit = blit
    this._bindGroup = bindGroup
    this._vw = vw
    this._vh = vh

    this.running = true
    this._video = video
    this._runFrame()
    return true
  }

  /** Bucle por rVFC. Reusa this._pipeline/_inputTexture/_blit/_bindGroup/_ctx,
   * ya sea recién creados o cacheados de un start() anterior con la misma
   * clave (tier+resolución) — así reabrir el shader no recompila nada. */
  _runFrame() {
    let errCount = 0
    const { device } = this
    const frame = () => {
      if (!this.running) return
      const video = this._video
      try {
        const vf = new VideoFrame(video)
        try {
          device.queue.copyExternalImageToTexture(
            { source: vf }, { texture: this._inputTexture }, [this._vw, this._vh])
        } finally {
          vf.close()
        }
        const encoder = device.createCommandEncoder()
        this._pipeline.pass(encoder)
        const rp = encoder.beginRenderPass({
          colorAttachments: [{
            view: this._ctx.getCurrentTexture().createView(),
            loadOp: 'clear', storeOp: 'store', clearValue: { r: 0, g: 0, b: 0, a: 1 },
          }],
        })
        rp.setPipeline(this._blit)
        rp.setBindGroup(0, this._bindGroup)
        rp.draw(3)
        rp.end()
        device.queue.submit([encoder.finish()])
        this.frames++
        errCount = 0
      } catch (e) {
        // frames sueltos pueden fallar en seeks; errores SOSTENIDOS = pipeline
        // rota → apagar el shader y avisar (nunca dejar el canvas en negro)
        if (++errCount === 1) console.warn('[a4k] error de frame:', e)
        if (errCount > 30) {
          console.error('[a4k] errores sostenidos — apagando shader:', e)
          this._cacheKey = null   // no reusar un pipeline que dio errores sostenidos
          this.stop()
          this.onFatal?.(e)
          return
        }
      }
      this._vfcHandle = video.requestVideoFrameCallback(frame)
    }
    this._vfcHandle = this._video.requestVideoFrameCallback(frame)
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
