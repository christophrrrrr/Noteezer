// Small enhancements on top of plain forms. Every action still works without it.
(function () {
  // The service worker makes Noteezer installable as a phone app.
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  // Every form also sends the browser's timezone, so "friday 3pm" and the
  // date picker are read in the user's time, not the server's.
  var zone = "";
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (error) {}
  document.querySelectorAll("form[method=post]").forEach(function (form) {
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "tz";
    input.value = zone;
    form.appendChild(input);
  });

  // Forms that delete or undo something ask first.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  // The folder sidebar slides in on a phone.
  document.querySelectorAll("[data-open-sidebar]").forEach(function (button) {
    button.addEventListener("click", function () { document.body.classList.add("sidebar-open"); });
  });
  document.querySelectorAll("[data-close-sidebar]").forEach(function (button) {
    button.addEventListener("click", function () { document.body.classList.remove("sidebar-open"); });
  });

  // Only one "..." menu is open at a time, and a click outside closes it.
  var menus = document.querySelectorAll("details.menu");
  menus.forEach(function (menu) {
    menu.addEventListener("toggle", function () {
      if (!menu.open) return;
      menus.forEach(function (other) { if (other !== menu) other.open = false; });
    });
  });
  document.addEventListener("click", function (event) {
    menus.forEach(function (menu) { if (menu.open && !menu.contains(event.target)) menu.open = false; });
  });

  var composer = document.querySelector("form[data-capture]");
  if (composer) {
    composer.querySelectorAll("[data-toggle-options]").forEach(function (button) {
      button.addEventListener("click", function () {
        composer.querySelector(".options").toggleAttribute("hidden");
      });
    });
    var text = composer.querySelector("textarea");
    var file = composer.querySelector("input[type=file]");
    var attached = composer.querySelector("[data-attached]");
    var attachedName = composer.querySelector("[data-attached-name]");

    // The box grows with the text. Before layout scrollHeight is 0, so the
    // CSS min-height stays in charge until there is something to measure.
    var grow = function () {
      text.style.height = "auto";
      if (text.scrollHeight) text.style.height = Math.min(text.scrollHeight, window.innerHeight * 0.4) + "px";
    };
    text.addEventListener("input", grow);
    window.addEventListener("load", grow);
    grow();

    // On a laptop Enter sends and Shift+Enter starts a new line, like a chat.
    // On a phone Enter keeps making new lines and the arrow button sends.
    var finePointer = window.matchMedia("(pointer: fine)").matches;
    text.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey && finePointer && !event.isComposing) {
        event.preventDefault();
        if (text.value.trim() || (file && file.files.length)) composer.requestSubmit();
      }
    });

    var showFile = function () {
      if (!attached) return;
      var name = file && file.files.length ? file.files[0].name : "";
      attached.hidden = !name;
      attachedName.textContent = name;
    };
    if (file) file.addEventListener("change", showFile);
    composer.querySelectorAll("[data-clear-file]").forEach(function (button) {
      button.addEventListener("click", function () { file.value = ""; showFile(); });
    });

    // A screenshot pasted into the text box is attached to the image field.
    composer.addEventListener("paste", function (event) {
      var files = event.clipboardData ? Array.from(event.clipboardData.files) : [];
      var picture = files.find(function (item) { return item.type.indexOf("image/") === 0; });
      if (!picture || !file) return;
      var transfer = new DataTransfer();
      transfer.items.add(picture);
      file.files = transfer.files;
      showFile();
      event.preventDefault();
    });
  }

  // Like a chat, the page opens at the newest line, just above the composer.
  // Images load after that and push the bottom down, so the page stays
  // pinned to the bottom for a moment unless the user starts scrolling.
  if (!window.location.hash) {
    var pinned = true;
    var unpin = function () { pinned = false; };
    ["wheel", "touchstart", "keydown"].forEach(function (name) {
      window.addEventListener(name, unpin, { once: true, passive: true });
    });
    var stick = function () { if (pinned) window.scrollTo(0, document.body.scrollHeight); };
    stick();
    window.addEventListener("load", stick);
    if ("ResizeObserver" in window) {
      var watcher = new ResizeObserver(stick);
      watcher.observe(document.body);
      window.setTimeout(function () { watcher.disconnect(); }, 3000);
    }
  }

  // While a line is still being sorted, reload to show the result, but never
  // while the user is typing or has a menu open, and not forever.
  if (document.querySelector("[data-pending]")) {
    var key = "noteezer-refreshes:" + window.location.search;
    var tries = Number(sessionStorage.getItem(key) || 0);
    if (tries < 12) {
      window.setTimeout(function () {
        var typing = composer && composer.querySelector("textarea").value.trim();
        var busy = document.querySelector("details[open]") || document.activeElement.matches("input, textarea, select");
        if (!typing && !busy && document.visibilityState === "visible") {
          sessionStorage.setItem(key, String(tries + 1));
          window.location.reload();
        }
      }, 4000);
    }
  } else {
    sessionStorage.removeItem("noteezer-refreshes:" + window.location.search);
  }
})();
