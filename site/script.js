(() => {
  "use strict";

  // Image safety net: if any Unsplash photo 404s, swap to a deterministic
  // Picsum placeholder so the demo never shows a broken image. Logs a hint
  // so the maintainer knows which URL to replace.
  const seenFallbacks = new Set();
  document.querySelectorAll("img").forEach((img, i) => {
    img.addEventListener(
      "error",
      () => {
        if (seenFallbacks.has(img)) return;
        seenFallbacks.add(img);
        const seed = encodeURIComponent((img.alt || "pacheco-" + i).slice(0, 40));
        const w = img.naturalWidth > 0 ? img.naturalWidth : 1200;
        const h = Math.round(w * 0.66);
        // eslint-disable-next-line no-console
        console.warn("[image fallback] swap this URL in index.html:", img.src);
        img.src = `https://picsum.photos/seed/${seed}/${w}/${h}`;
      },
      { once: true }
    );
  });

  const nav = document.getElementById("nav");
  const burger = nav.querySelector(".nav__burger");
  const mobile = document.getElementById("mobileMenu");

  // Sticky-nav background after a small scroll.
  const onScroll = () => {
    nav.classList.toggle("is-stuck", window.scrollY > 24);
  };
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  // Mobile menu toggle.
  const setMenu = (open) => {
    nav.classList.toggle("is-open", open);
    burger.setAttribute("aria-expanded", String(open));
    mobile.hidden = !open;
  };
  burger.addEventListener("click", () => {
    setMenu(burger.getAttribute("aria-expanded") !== "true");
  });
  mobile.querySelectorAll("a").forEach((a) =>
    a.addEventListener("click", () => setMenu(false))
  );

  // Scroll reveal.
  const revealTargets = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -60px 0px" }
    );
    revealTargets.forEach((el) => io.observe(el));
  } else {
    revealTargets.forEach((el) => el.classList.add("in"));
  }

  // Footer year.
  const yr = document.getElementById("yr");
  if (yr) yr.textContent = String(new Date().getFullYear());

  // Lightbox for portfolio tiles.
  const lb = document.getElementById("lightbox");
  const lbImg = lb.querySelector("img");
  const lbClose = lb.querySelector(".lightbox__close");

  const openLightbox = (src, alt) => {
    lbImg.src = src;
    lbImg.alt = alt || "";
    lb.hidden = false;
    document.body.style.overflow = "hidden";
  };
  const closeLightbox = () => {
    lb.hidden = true;
    lbImg.src = "";
    document.body.style.overflow = "";
  };

  document.querySelectorAll("[data-zoom]").forEach((tile) => {
    tile.addEventListener("click", () => {
      const img = tile.querySelector("img");
      if (!img) return;
      // Swap to a higher-res variant when possible (Unsplash w= param).
      const big = img.src.replace(/([?&])w=\d+/, "$1w=1800");
      openLightbox(big, img.alt);
    });
  });
  lbClose.addEventListener("click", closeLightbox);
  lb.addEventListener("click", (e) => {
    if (e.target === lb) closeLightbox();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !lb.hidden) closeLightbox();
  });
})();
