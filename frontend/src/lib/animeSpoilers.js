export const hideEpisodeSpoilers = (enabled, episode, revealed) => !!enabled && !episode.watched && !revealed
