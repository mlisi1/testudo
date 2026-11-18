// The docs' videos behave like GIFs. Instant navigation inserts a page without
// applying the `muted` attribute, and browsers only autoplay muted videos.
document$.subscribe(() => {
  document.querySelectorAll("video.tx-loop").forEach((video) => {
    video.muted = true;
    video.play().catch(() => {});
  });
});
