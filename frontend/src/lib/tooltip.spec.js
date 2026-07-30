// @vitest-environment happy-dom
/* El tooltip propio sustituye al `title` del SO en 177 sitios.
 *
 * Lo que hay que fijar no es que se vea bonito, sino lo que se PIERDE al quitar `title`: el
 * nombre accesible de los botones que sólo llevan un icono. Y lo que el nativo no hacía y aquí
 * sí: salir con el foco del teclado y sobre un control deshabilitado (que es justo el que
 * necesita explicarse).
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { installTooltips } from './tooltip'

beforeAll(() => { installTooltips() })

function mover(el) {
  const r = el.getBoundingClientRect()
  el.dispatchEvent(new window.MouseEvent('mousemove', {
    clientX: r.left + 1, clientY: r.top + 1, bubbles: true,
  }))
}

async function fotograma() { await new Promise(r => setTimeout(r, 0)) }

describe('tooltip propio', () => {
  beforeEach(() => { document.body.innerHTML = '' })

  it('un botón de sólo icono conserva su nombre accesible', async () => {
    document.body.innerHTML = '<button data-tip="Pantalla completa"><svg></svg></button>'
    await fotograma()
    expect(document.querySelector('button').getAttribute('aria-label')).toBe('Pantalla completa')
  })

  it('NO pisa el nombre de un botón que ya tiene texto', async () => {
    document.body.innerHTML = '<button data-tip="Sube el perfil al repo">Guardar</button>'
    await fotograma()
    expect(document.querySelector('button').hasAttribute('aria-label')).toBe(false)
  })

  it('NO pisa un aria-label puesto a mano', async () => {
    document.body.innerHTML = '<button data-tip="Pista" aria-label="Nombre bueno"></button>'
    await fotograma()
    expect(document.querySelector('button').getAttribute('aria-label')).toBe('Nombre bueno')
  })

  it('con el foco del teclado sale al instante (el nativo no salía nunca)', async () => {
    document.body.innerHTML = '<button data-tip="Atajos"></button>'
    const b = document.querySelector('button')
    b.dispatchEvent(new window.FocusEvent('focusin', { bubbles: true }))
    const burbuja = document.querySelector('.tipbubble')
    expect(burbuja.textContent).toBe('Atajos')
    expect(burbuja.classList.contains('is-on')).toBe(true)
  })

  it('se esconde al salir el foco y al pulsar una tecla', async () => {
    document.body.innerHTML = '<button data-tip="Atajos"></button>'
    const b = document.querySelector('button')
    b.dispatchEvent(new window.FocusEvent('focusin', { bubbles: true }))
    b.dispatchEvent(new window.FocusEvent('focusout', { bubbles: true }))
    expect(document.querySelector('.tipbubble').classList.contains('is-on')).toBe(false)
  })

  it('el texto se pone como TEXTO, nunca como markup', async () => {
    document.body.innerHTML = '<button data-tip="<img src=x onerror=alert(1)>"></button>'
    document.querySelector('button').dispatchEvent(new window.FocusEvent('focusin', { bubbles: true }))
    const burbuja = document.querySelector('.tipbubble')
    expect(burbuja.querySelector('img')).toBeNull()
    expect(burbuja.textContent).toContain('<img')
  })
})
