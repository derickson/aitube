declare namespace YT {
  enum PlayerState {
    UNSTARTED = -1,
    ENDED = 0,
    PLAYING = 1,
    PAUSED = 2,
    BUFFERING = 3,
    CUED = 5,
  }

  interface PlayerVars {
    start?: number;
    autoplay?: number;
    rel?: number;
  }

  interface OnStateChangeEvent {
    data: number;
    target: Player;
  }

  interface PlayerEvents {
    onReady?: (event: { target: Player }) => void;
    onStateChange?: (event: OnStateChangeEvent) => void;
  }

  class Player {
    constructor(
      elementId: string,
      options: {
        videoId: string;
        playerVars?: PlayerVars;
        events?: PlayerEvents;
      },
    );
    getCurrentTime(): number;
    getDuration(): number;
    seekTo(seconds: number, allowSeekAhead?: boolean): void;
    destroy(): void;
  }
}
