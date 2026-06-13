-- skip-intro.lua — floating "Saltar OP" button + key for the Manga/Anime app.
-- Jumps a fixed amount forward (default 90s = 1:30), independent of AniSkip detection.
-- The button behaves like mpv's on-screen controls: it only appears when you MOVE the
-- cursor (during the first `window` seconds) and auto-hides after `idle` seconds of no
-- movement — so it never sits on top of the video. A key works any time.
--   --script-opts=skip-intro-skip=90,skip-intro-window=240
local mp = require 'mp'
local assdraw = require 'mp.assdraw'

local opts = { skip = 90, window = 240, idle = 3, key = 'Tab' }
require('mp.options').read_options(opts, 'skip-intro')

local overlay = mp.create_osd_overlay('ass-events')
local in_window, shown, hover = false, false, false
local hide_timer = nil
local last_x, last_y = -1, -1

-- forward declarations (these reference each other)
local rect, draw, do_skip, set_hover, reveal, hide, schedule_hide

rect = function()
  local d = mp.get_property_native('osd-dimensions')
  if not d or not d.w or d.w == 0 then return nil end
  local bw, bh = 224, 56
  local x2, y2 = d.w - 48, d.h - 120   -- bottom-right, above the OSC bar
  return d.w, d.h, x2 - bw, y2 - bh, x2, y2
end

draw = function()
  local W, H, x1, y1, x2, y2 = rect()
  if not (shown and W) then overlay.data = ''; overlay:update(); return end
  overlay.res_x, overlay.res_y = W, H
  local a = assdraw.ass_new()
  a:new_event()
  local fill = hover and '&H30221a&' or '&H140d0a&'
  a:append('{\\bord2\\3c&Hff8d4d&\\1c' .. fill .. '\\1a&H0E&\\shad0}')
  a:pos(0, 0); a:draw_start()
  if a.round_rect_cw then a:round_rect_cw(x1, y1, x2, y2, 12) else a:rect_cw(x1, y1, x2, y2) end
  a:draw_stop()
  a:new_event()
  a:pos((x1 + x2) / 2, (y1 + y2) / 2); a:an(5)
  a:append('{\\fs28\\b1\\1c&HFFFFFF&\\bord0\\shad1}\\h\\h\226\143\173  Saltar OP\\h\\h')
  overlay.data = a.text
  overlay:update()
end

do_skip = function()
  mp.commandv('seek', tostring(opts.skip), 'relative')
  mp.osd_message('\226\143\173  +' .. opts.skip .. 's', 1)
  hide()   -- past the opening now
end

set_hover = function(h)
  if h == hover then return end
  hover = h
  if h then mp.add_forced_key_binding('MBTN_LEFT', 'skipop-click', do_skip)
  else mp.remove_key_binding('skipop-click') end
  draw()
end

schedule_hide = function()
  if hide_timer then hide_timer:kill() end
  hide_timer = mp.add_timeout(opts.idle, function()
    if not hover then hide() end   -- keep it while the cursor rests on the button
  end)
end

reveal = function()
  if not in_window then return end
  if not shown then shown = true; draw() end   -- draw once on appear (hover/resize redraw separately)
  schedule_hide()
end

hide = function()
  if hide_timer then hide_timer:kill(); hide_timer = nil end
  if not shown and not hover then return end
  shown = false
  set_hover(false)
  overlay.data = ''
  overlay:update()
end

-- Show on cursor movement; hover-test (MBTN_LEFT is only captured over the button).
mp.observe_property('mouse-pos', 'native', function(_, m)
  if not m then return end
  local moved = (m.x ~= last_x or m.y ~= last_y)
  last_x, last_y = m.x, m.y
  if moved then reveal() end
  if shown then
    local W, H, x1, y1, x2, y2 = rect()
    if W then set_hover(m.x >= x1 and m.x <= x2 and m.y >= y1 and m.y <= y2) end
  end
end)

mp.observe_property('time-pos', 'number', function(_, t)
  if t == nil then return end
  in_window = t < opts.window
  if not in_window then hide() end
end)

mp.observe_property('osd-dimensions', 'native', function() if shown then draw() end end)

-- Keyboard shortcut works at any time, even when the button is hidden.
mp.add_forced_key_binding(opts.key, 'skip-op-key', do_skip)
