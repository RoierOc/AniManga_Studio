-- skip-intro.lua — floating "Saltar OP" button + key for the Manga/Anime app.
-- Jumps a fixed amount forward (default 90s = 1:30), independent of AniSkip detection.
-- The button shows during the first `window` seconds of the episode (covers cold-opens),
-- and a key works any time. Configure via:
--   --script-opts=skip-intro-skip=90,skip-intro-window=240
local mp = require 'mp'
local assdraw = require 'mp.assdraw'

local opts = { skip = 90, window = 240, key = 'Tab' }
require('mp.options').read_options(opts, 'skip-intro')

local overlay = mp.create_osd_overlay('ass-events')
local shown, hover = false, false

-- forward declarations (these reference each other)
local rect, draw, do_skip, set_hover, show, hide

rect = function()
  local d = mp.get_property_native('osd-dimensions')
  if not d or not d.w or d.w == 0 then return nil end
  local bw, bh = 224, 56
  local x2, y2 = d.w - 48, d.h - 88
  return d.w, d.h, x2 - bw, y2 - bh, x2, y2
end

draw = function()
  local W, H, x1, y1, x2, y2 = rect()
  if not (shown and W) then overlay.data = ''; overlay:update(); return end
  overlay.res_x, overlay.res_y = W, H
  local a = assdraw.ass_new()
  -- pill background (brighter on hover)
  a:new_event()
  local fill = hover and '&H30221a&' or '&H140d0a&'
  a:append('{\\bord2\\3c&Hff8d4d&\\1c' .. fill .. '\\1a&H0E&\\shad0}')
  a:pos(0, 0); a:draw_start()
  if a.round_rect_cw then a:round_rect_cw(x1, y1, x2, y2, 12) else a:rect_cw(x1, y1, x2, y2) end
  a:draw_stop()
  -- label
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

show = function()
  if not shown then shown = true end
  draw()
end

hide = function()
  if not shown and not hover then return end
  shown = false
  set_hover(false)
  overlay.data = ''
  overlay:update()
end

-- Only capture MBTN_LEFT while the cursor is over the button → the OSC/seek bar
-- keeps working everywhere else.
mp.observe_property('mouse-pos', 'native', function(_, m)
  if not shown or not m then if hover then set_hover(false) end; return end
  local W, H, x1, y1, x2, y2 = rect()
  if not W then return end
  set_hover(m.x >= x1 and m.x <= x2 and m.y >= y1 and m.y <= y2)
end)

mp.observe_property('time-pos', 'number', function(_, t)
  if t == nil then return end
  if t < opts.window then show() else hide() end
end)

mp.observe_property('osd-dimensions', 'native', function() if shown then draw() end end)

-- Keyboard shortcut works at any time.
mp.add_forced_key_binding(opts.key, 'skip-op-key', do_skip)
