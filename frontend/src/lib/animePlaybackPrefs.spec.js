import { describe, expect, it } from 'vitest'
import {
  patchAnimePlaybackPrefs,
  readAnimePlaybackPrefs,
  resolveTrackSelection,
  trackIdentity,
} from './animePlaybackPrefs'

describe('preferencias de reproducción por anime', () => {
  it('recupera las pistas equivalentes aunque cambie su índice', () => {
    const english = { lang: 'eng', title: 'English Stereo', codec: 'aac' }
    const spanish = { lang: 'spa', title: 'Español latino', codec: 'aac' }
    const prefs = { audioTrack: trackIdentity(english), subTrack: trackIdentity(spanish) }

    expect(resolveTrackSelection(
      [{ lang: 'jpn', title: 'Japanese' }, { ...english, codec: 'flac' }],
      [{ lang: 'eng', title: 'English' }, spanish], prefs, 1,
    )).toEqual({ audioIndex: 1, subIndex: 1 })
  })

  it('usa el audio inicial y el subtítulo español predeterminado si falta la pista guardada', () => {
    const selected = resolveTrackSelection(
      [{ lang: 'jpn', title: 'Japanese' }],
      [{ lang: 'spa', title: 'Español' }, { lang: 'eng', title: 'English' }],
      { audioTrack: 'embedded:en|english', subTrack: 'embedded:fr|francés' },
      1,
    )

    expect(selected).toEqual({ audioIndex: 0, subIndex: 0 })
  })

  it('respeta explícitamente sin subtítulos y guarda preferencias por ID', () => {
    const values = new Map()
    const storage = {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, String(value)),
    }
    patchAnimePlaybackPrefs(42, { subTrack: 'off', tier: 'maximo' }, storage)
    patchAnimePlaybackPrefs(42, { audioTrack: 'embedded:en|english' }, storage)

    expect(readAnimePlaybackPrefs(42, storage)).toEqual({
      subTrack: 'off', tier: 'maximo', audioTrack: 'embedded:en|english',
    })
    expect(resolveTrackSelection([], [{ lang: 'spa', title: 'Español' }],
      readAnimePlaybackPrefs(42, storage), 1).subIndex).toBe(-1)
    expect(readAnimePlaybackPrefs(7, storage)).toEqual({})
  })
})
