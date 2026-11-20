// Noteezer's static UI (ADR-6). It talks to two services:
//   inbox     — accounts, lines, folders, sorting, images (everything but times)
//   reminders — done and snooze on a reminder (ADR-6)
// Their addresses come from config.js, which is written at deploy time.
//
// Safety: text from notes, folders, and link previews is only ever put on the
// page with textContent, never as HTML, so a note cannot inject markup. The
// only markup set from strings is the fixed icon shapes below.
(function () {
  "use strict";

  var config = window.NOTEEZER || {};
  var INBOX = String(config.inboxUrl || "").replace(/\/+$/, "");
  var REMINDERS = String(config.remindersUrl || "").replace(/\/+$/, "");
  var TOKEN_KEY = "noteezer-token";
  var SHARE_CACHE = "noteezer-share";
  var KINDS = ["note", "idea", "task", "link", "reminder"];

  // ---------- small helpers ----------

  var ICONS = {
    layers: '<path d="m12 3 9 5-9 5-9-5 9-5z"/><path d="m3 13 9 5 9-5"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    circle: '<circle cx="12" cy="12" r="8"/>',
    check: '<circle cx="12" cy="12" r="8"/><path d="m8.5 12.2 2.4 2.4 4.6-4.9"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l2.5 2"/>',
    link: '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1.5-1.5"/>',
    bulb: '<path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1.1 2.2h5c.1-1 .5-1.7 1.1-2.2A6 6 0 0 0 12 3z"/>',
    note: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
    dots: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    clip: '<path d="m21 11-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l8-8"/>',
    send: '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    sparkle: '<path d="M12 3.5 13.9 9l5.6 2-5.6 2L12 18.5 10.1 13l-5.6-2 5.6-2z"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  };

  function icon(name, size) {
    size = size || 16;
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("width", size);
    svg.setAttribute("height", size);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[name] || ""; // fixed shapes from this file only
    return svg;
  }

  // h("a", {href: "/x", class: "chip"}, "text", child) builds an element.
  // Strings become text nodes, so they are never read as HTML.
  function h(tag, attributes) {
    var element = document.createElement(tag);
    var attrs = attributes || {};
    Object.keys(attrs).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key.indexOf("on") === 0) element.addEventListener(key.slice(2), value);
      else if (key === "style") Object.keys(value).forEach(function (name) { element.style.setProperty(name, value[name]); });
      else element.setAttribute(key, value === true ? "" : value);
    });
    for (var index = 2; index < arguments.length; index++) append(element, arguments[index]);
    return element;
  }

  function append(parent, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) child.forEach(function (one) { append(parent, one); });
    else parent.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
  }

  function $(selector) { return document.querySelector(selector); }

  function safeUrl(value) {
    // Only web links become clickable; anything else (javascript:, data:) does not.
    try {
      var url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch (error) {
      return null;
    }
  }

  function browserZone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (error) { return ""; }
  }

  // ---------- the token ----------

  function getToken() {
    try { return localStorage.getItem(TOKEN_KEY); } catch (error) { return null; }
  }

  function setToken(value) {
    try {
      if (value) localStorage.setItem(TOKEN_KEY, value);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (error) { /* private mode: the session lasts until the tab closes */ }
    memoryToken = value;
  }

  var memoryToken = getToken();

  // ---------- calling the services ----------

  function ApiError(status, message) {
    this.status = status;
    this.message = message;
  }
  ApiError.prototype = Object.create(Error.prototype);

  // ---------- cold starts ----------
  //
  // On Azure both services scale to zero and their databases pause after an
  // hour. The first request after a quiet period starts a container, which
  // may wait about a minute for its database: meanwhile Azure answers 503 "App
  // port is not ready" (without CORS headers, so the browser only sees a
  // failed fetch), or the service's own /health answers 503. A GET is retried
  // with a growing wait, about 90 seconds in all, under a "starting up"
  // banner. Other methods are not retried, because a request that timed out
  // may still have been saved; they say what happened instead.

  var STARTING_WAITS = [2, 3, 5, 8, 12, 15, 15, 15, 15]; // seconds
  var startingBanner = document.querySelector("[data-starting]");
  var startingWaits = 0;

  function showStarting(change) {
    startingWaits += change;
    startingBanner.hidden = startingWaits <= 0;
  }

  // A 503 from the services' own code carries a JSON "detail" (reminders is
  // down, for example) and is final. Anything else from 502 to 504 means the
  // service or its database is still starting.
  function stillStarting(response) {
    if ([502, 503, 504].indexOf(response.status) < 0) return Promise.resolve(false);
    return response.clone().json()
      .then(function (data) { return typeof data.detail !== "string"; })
      .catch(function () { return true; });
  }

  function sendWithRetries(url, init) {
    var retry = init.method === "GET";
    var attempt = 0;
    var showing = false;

    function stopShowing() {
      if (showing) { showing = false; showStarting(-1); }
    }
    function later(failure) {
      if (!retry || attempt >= STARTING_WAITS.length) return failure();
      if (!showing) { showing = true; showStarting(1); }
      var seconds = STARTING_WAITS[attempt++];
      return new Promise(function (resolve) { setTimeout(resolve, seconds * 1000); }).then(once);
    }
    function once() {
      return fetch(url, init).then(function (response) {
        return stillStarting(response).then(function (starting) {
          return starting ? later(function () { return response; }) : response;
        });
      }, function (error) {
        return later(function () { throw error; });
      });
    }
    return once().then(
      function (response) { stopShowing(); return response; },
      function (error) { stopShowing(); throw error; }
    );
  }

  function call(base, path, options) {
    options = options || {};
    var headers = {};
    if (memoryToken) headers.Authorization = "Bearer " + memoryToken;
    var body = options.body;
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.json);
    }
    var method = options.method || "GET";
    return sendWithRetries(base + path, { method: method, headers: headers, body: body })
      .catch(function () {
        throw new ApiError(0, base === REMINDERS
          ? "Reminders are unavailable right now. Try again in a moment."
          : method === "GET"
            ? "Noteezer is still starting up or cannot be reached. Check your connection and try again in a minute."
            : "Noteezer could not be reached, so this may not have been saved. It may be starting up after a quiet period: wait a moment, reload, and try again.");
      })
      .then(function (response) {
        if ([502, 503, 504].indexOf(response.status) >= 0 && method === "GET") {
          // Still starting after every retry.
          return response.json().catch(function () { return {}; }).then(function (data) {
            throw new ApiError(response.status, typeof data.detail === "string" ? data.detail
              : "Noteezer is still starting up. Try again in a minute.");
          });
        }
        if (response.status === 401 && memoryToken) {
          setToken(null);
          showAuth("Your login ran out. Log in again.");
          throw new ApiError(401, "Log in again.");
        }
        if (!response.ok) {
          return response.json().catch(function () { return {}; }).then(function (data) {
            var detail = typeof data.detail === "string" ? data.detail : "Something went wrong (" + response.status + ").";
            throw new ApiError(response.status, detail);
          });
        }
        if (response.status === 204) return null;
        var type = response.headers.get("content-type") || "";
        return type.indexOf("application/json") === 0 ? response.json() : response.blob();
      });
  }

  function inbox(path, options) { return call(INBOX, path, options); }
  function reminders(path, options) { return call(REMINDERS, path, options); }

  // ---------- state ----------

  var state = {
    folders: [],
    unsortedCount: 0,
    total: 0,
    items: [],
    remindersDown: false,
    folder: "",
    query: "",
    polls: 0,
  };
  var imageUrls = {}; // item id -> object URL of its image, fetched with the token
  var pollTimer = null;

  function readLocation() {
    var params = new URLSearchParams(window.location.search);
    state.folder = params.get("folder") || "";
    state.query = params.get("q") || "";
  }

  function go(folder, query) {
    var params = new URLSearchParams();
    if (folder) params.set("folder", folder);
    if (query) params.set("q", query);
    var search = params.toString();
    window.history.pushState(null, "", "/" + (search ? "?" + search : ""));
    readLocation();
    document.body.classList.remove("sidebar-open");
    load({ toBottom: true });
  }

  function currentFolder() {
    return state.folders.find(function (entry) { return String(entry.id) === state.folder; }) || null;
  }

  function hasFolders() { return state.folders.length > 0; }

  // ---------- loading ----------

  function load(options) {
    options = options || {};
    var params = new URLSearchParams();
    if (state.query) params.set("q", state.query);
    if (state.folder) params.set("folder", state.folder);
    return Promise.all([inbox("/api/folders"), inbox("/api/items?" + params.toString())])
      .then(function (results) {
        state.folders = results[0].folders;
        state.unsortedCount = results[0].unsorted_count;
        state.total = results[0].total_count;
        state.items = results[1].items;
        state.remindersDown = results[1].reminders_down;
        if (!options.keepError) showError(null);
        render();
        if (options.toBottom) stickToBottom();
        schedulePoll();
      })
      .catch(function (error) {
        if (error.status === 404 && state.folder) return go("", ""); // the folder is gone
        if (error.status !== 401) showError(error.message);
      });
  }

  // While a line is still being sorted, ask again every few seconds, but
  // never while the person is typing or has a menu open, and not forever.
  function schedulePoll() {
    window.clearTimeout(pollTimer);
    if (!state.items.some(isWaiting) || state.polls >= 12) return;
    pollTimer = window.setTimeout(function () {
      var composer = $("form[data-capture]");
      var typing = composer.body.value.trim();
      var busy = document.querySelector("details[open]") ||
        (document.activeElement && document.activeElement.matches("input, textarea, select"));
      if (typing || busy || document.visibilityState !== "visible") return schedulePoll();
      state.polls += 1;
      load({ keepError: true });
    }, 4000);
  }

  // Every action changes something, then reloads what is shown.
  function act(promise, options) {
    state.polls = 0;
    return promise
      .then(function () { return load(options); })
      .catch(function (error) {
        if (error.status !== 401) showError(error.message);
      });
  }

  function showError(message) {
    var banner = $("[data-error]");
    banner.textContent = message || "";
    banner.hidden = !message;
  }

  // ---------- rendering ----------

  function render() {
    var current = currentFolder();
    renderTree(current);
    renderHeading(current);

    $("[data-reminders-down]").hidden = !state.remindersDown;
    var banner = $("[data-unsorted-banner]");
    banner.replaceChildren();
    banner.hidden = state.folder !== "unsorted";
    if (state.folder === "unsorted") {
      if (hasFolders()) {
        append(banner, [
          h("span", null, "New lines are sorted into your folders automatically. Anything unclear waits here."),
          h("button", { class: "button", type: "button", onclick: function () {
            act(inbox("/api/sorting/retry", { method: "POST" }));
          } }, "Sort again"),
        ]);
      } else {
        append(banner, "Create a folder and new lines will be sorted into it automatically.");
      }
    }

    var subfolders = current ? state.folders.filter(function (entry) { return entry.parent_id === current.id; }) : [];
    var nav = $("[data-subfolders]");
    nav.replaceChildren();
    nav.hidden = !subfolders.length;
    subfolders.forEach(function (entry) {
      append(nav, folderLink(entry.id, [icon("folder", 14), entry.name, " ", h("span", { class: "count" }, entry.count || "")]));
    });

    renderFeed(current);
    $("form[data-capture] textarea").placeholder = current
      ? "Write a line in " + current.name
      : "Write a line, or paste a screenshot";
  }

  function folderLink(folder, children, className) {
    var target = folder === "" ? "/" : "/?folder=" + encodeURIComponent(folder);
    return h("a", {
      href: target,
      class: className,
      onclick: function (event) {
        if (event.metaKey || event.ctrlKey) return;
        event.preventDefault();
        go(String(folder), "");
      },
    }, children);
  }

  function renderTree(current) {
    var tree = $("[data-tree]");
    tree.replaceChildren(
      folderLink("", [icon("layers"), h("span", null, "Everything"), h("span", { class: "count" }, state.total)],
        "tree-row" + (state.folder === "" ? " active" : "")),
      folderLink("unsorted", [icon("inbox"), h("span", null, "Unsorted"),
        h("span", { class: "count" + (state.unsortedCount ? " badge" : "") }, state.unsortedCount)],
        "tree-row" + (state.folder === "unsorted" ? " active" : "")),
      h("div", { class: "tree-label" }, "Folders")
    );
    if (!hasFolders()) {
      append(tree, h("p", { class: "tree-empty" }, "No folders yet. New lines wait in Unsorted until you add one."));
    }
    state.folders.forEach(function (entry) {
      var link = folderLink(entry.id, [icon("folder"), h("span", null, entry.name), h("span", { class: "count" }, entry.count || "")],
        "tree-row" + (current && entry.id === current.id ? " active" : ""));
      link.style.setProperty("--depth", entry.depth);
      append(tree, link);
    });

    var parent = $("[data-parent-select]");
    parent.replaceChildren(h("option", { value: "" }, "Top level"));
    state.folders.forEach(function (entry) {
      append(parent, h("option", { value: entry.id, selected: current && entry.id === current.id }, "Inside " + entry.path));
    });
    if (!hasFolders()) $("[data-new-folder]").open = true;
  }

  function renderHeading(current) {
    var crumbs = $("[data-crumbs]");
    crumbs.replaceChildren();
    var trail = [];
    var byId = {};
    state.folders.forEach(function (entry) { byId[entry.id] = entry; });
    var parentId = current ? current.parent_id : null;
    while (parentId !== null && byId[parentId]) {
      trail.unshift(byId[parentId]);
      parentId = byId[parentId].parent_id;
    }
    trail.forEach(function (crumb) { append(crumbs, [folderLink(crumb.id, crumb.name), " / "]); });
    crumbs.hidden = !trail.length;

    var title = current ? current.name : state.folder === "unsorted" ? "Unsorted" : "Everything";
    $("[data-title]").textContent = title;
    document.title = title + " · Noteezer";
    $("[data-search] input").value = state.query;

    var menu = $("[data-folder-menu]");
    menu.hidden = !current;
    menu.open = false;
    if (current) {
      $("[data-rename-form] input").value = current.name;
      var move = $("[data-move-form] select");
      move.replaceChildren(h("option", { value: "" }, "Top level"));
      state.folders.forEach(function (entry) {
        if (entry.id !== current.id) {
          append(move, h("option", { value: entry.id, selected: entry.id === current.parent_id }, entry.path));
        }
      });
    }
  }

  function renderFeed(current) {
    var feed = $("[data-feed]");
    feed.replaceChildren();
    // Like a chat: due reminders are pinned on top, the rest run from oldest
    // to newest so the newest line sits just above the composer.
    var due = state.items.filter(function (item) { return item.status === "due"; });
    var timeline = state.items.filter(function (item) { return item.status !== "due"; }).reverse();
    if (due.length) {
      append(feed, h("div", { class: "divider due" }, "Due now"));
      due.forEach(function (item) { append(feed, card(item, current)); });
    }
    var lastDay = "";
    timeline.forEach(function (item) {
      var day = dayLabel(item.created_at);
      if (day !== lastDay) append(feed, h("div", { class: "divider" }, day));
      lastDay = day;
      append(feed, card(item, current));
    });
    if (!state.items.length) {
      append(feed, h("div", { class: "empty" }, state.query
        ? [h("strong", null, "Nothing matches “" + state.query + "”"), "Try another word."]
        : [h("strong", null, "Nothing here yet"), "Write a line below, paste a link, or add a screenshot."]));
    }
  }

  function dayLabel(createdAt) {
    var day = new Date(createdAt);
    var today = new Date();
    var start = function (date) { return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime(); };
    var difference = Math.round((start(today) - start(day)) / 86400000);
    if (difference === 0) return "Today";
    if (difference === 1) return "Yesterday";
    var options = { weekday: "short", day: "numeric", month: "short" };
    if (day.getFullYear() !== today.getFullYear()) options.year = "numeric";
    return day.toLocaleDateString("en-GB", options).replace(/,/g, "");
  }

  function isWaiting(item) {
    return !item.sort && (
      (hasFolders() && !item.folder_ids.length) ||
      (item.kind === "reminder" && !item.status && !item.reminder_unavailable) ||
      (item.image && item.image.ai_allowed && !item.image.read)
    );
  }

  function confirmThen(message, action) {
    return function () { if (window.confirm(message)) action(); };
  }

  function button(label, className, onclick, extra) {
    return h("button", Object.assign({ class: className, type: "button", onclick: onclick }, extra || {}), label);
  }

  function card(item, current) {
    var waiting = isWaiting(item);
    var id = item.id;
    var sort = item.sort;

    var lead;
    if (item.kind === "task") {
      lead = button(icon(item.task_done ? "check" : "circle", 18), "check", function () {
        act(inbox("/api/items/" + id + "/toggle", { method: "POST" }));
      }, { "aria-label": item.task_done ? "Mark as not done" : "Mark as done" });
    } else {
      var name = { reminder: "clock", link: "link", idea: "bulb" }[item.kind] || (item.image ? "image" : "note");
      lead = icon(name, 18);
    }

    var main = h("div", { class: "note-main" }, h("div", { class: "note-body" }, item.body));

    if (item.image) {
      var picture = h("img", { class: "shot", alt: item.image.title || "Saved image", loading: "lazy" });
      var open = h("a", { target: "_blank", rel: "noopener" }, picture);
      loadImage(id, picture, open);
      append(main, open);
      if (item.image.text) append(main, h("p", { class: "image-text" }, item.image.text));
      if (!item.image.ai_allowed) append(main, h("p", { class: "private" }, "Private image: not read by AI."));
    }

    if (item.preview) {
      var href = safeUrl(item.preview.url);
      append(main, h(href ? "a" : "div", { class: "preview", href: href, target: href && "_blank", rel: href && "noopener noreferrer" },
        h("strong", null, item.preview.title || ""),
        h("span", null, (item.preview.site || "") + (item.preview.description ? " · " + item.preview.description : ""))));
    }

    var meta = h("div", { class: "note-meta" }, h("span", { class: "chip" }, item.kind + (item.task_done ? " · done" : "")));
    item.folders.forEach(function (inFolder) {
      if (!current || inFolder.id !== current.id) {
        append(meta, folderLink(inFolder.id, [icon("folder", 12), inFolder.path], "chip"));
      }
    });
    if (!item.folders.length && !current && state.folder !== "unsorted") {
      append(meta, h("span", { class: "chip" }, icon("inbox", 12), "Unsorted"));
    }
    item.tags.forEach(function (tag) { append(meta, h("span", { class: "chip tag" }, "#" + tag)); });
    if (item.status) {
      // How often it was snoozed (reminders migration 002): "(2x)" while snoozed, else "\u00b7 snoozed 2x".
      var snoozes = !(item.snoozed_count > 0) ? ""
        : item.status === "snoozed" ? " (" + item.snoozed_count + "\u00d7)"
          : " \u00b7 snoozed " + item.snoozed_count + "\u00d7";
      append(meta, h("span", { class: "chip" + (item.status === "due" ? " due" : "") }, icon("clock", 12), item.status + " " + item.remind_label + snoozes));
    } else if (item.kind === "reminder") {
      var why = item.reminder_unavailable ? ", reminders is unavailable"
        : waiting ? ", reading it from the text…" : ", pick one under ⋯ Edit";
      append(meta, h("span", { class: "chip due" }, "no time yet" + why));
    }
    if (item.repeat_daily) append(meta, h("span", { class: "chip" }, "daily" + (item.repeat_until ? " until " + item.repeat_until : "")));
    if (item.folder_ids.length && sort && sort.status === "filed") append(meta, h("span", { class: "chip ai" }, icon("sparkle", 12), "filed by " + sort.engine));
    if (item.split_from) append(meta, h("span", { class: "chip ai" }, "split from a longer note"));
    append(main, meta);

    var suggest = suggestions(item, sort);
    if (suggest) append(main, suggest);

    return h("article", {
      class: "note" + (item.task_done ? " done" : "") + (item.status === "due" ? " is-due" : ""),
      id: "item-" + id,
      "data-pending": waiting,
    }, h("div", { class: "note-lead" }, lead), main, itemMenu(item));
  }

  function suggestions(item, sort) {
    var id = item.id;
    var unfiled = !item.folder_ids.length;
    var canAccept = unfiled && sort && sort.status === "review" && sort.folder_path;
    var canCreate = unfiled && sort && sort.status === "review" && sort.proposed_path && !sort.folder_path;
    var canRemind = sort && sort.remind_label && item.kind !== "reminder";
    var noteLine = unfiled && hasFolders();
    if (!(canAccept || canCreate || canRemind || item.split_from || noteLine || item.also.length)) return null;

    var box = h("div", { class: "suggest" });
    if (noteLine) {
      if (!sort) append(box, h("span", { class: "hint" }, "Sorting…"));
      else if (canAccept) append(box, h("span", null, "Suggested: " + sort.folder_path));
      else if (canCreate) append(box, h("span", null, "New folder? " + sort.proposed_path));
      else {
        var hints = {
          review: "No folder matched.",
          retry: "Sorting failed, trying again soon.",
          failed: "Could not be sorted.",
          accepted: "Kept as one note. Move it when you like.",
        };
        if (hints[sort.status]) append(box, h("span", { class: "hint" }, hints[sort.status]));
      }
    }
    var post = function (path) { return function () { act(inbox("/api/items/" + id + path, { method: "POST" })); }; };
    if (canAccept) append(box, button("Accept", "button primary", post("/accept")));
    if (canCreate) append(box, button("Create folder", "button primary", post("/create-folder")));
    if (canRemind) append(box, button([icon("clock", 14), "Remind me " + sort.remind_label], "button", post("/remind")));
    item.also.forEach(function (extra) {
      append(box, button([icon("plus", 14), "Also add to " + extra.path], "button", post("/also/" + extra.id)));
      append(box, button(icon("x", 14), "icon-button", function () {
        act(inbox("/api/items/" + id + "/also/" + extra.id, { method: "DELETE" }));
      }, { "aria-label": "Not in " + extra.path }));
    });
    if (item.split_from) {
      append(box, button("Undo split", "button ghost",
        confirmThen("Put the original note back and remove all of its parts?", post("/unsplit"))));
    }
    return box;
  }

  function itemMenu(item) {
    var id = item.id;
    var panel = h("div", { class: "menu-panel" });

    if (item.kind === "reminder") {
      // Done and snooze go straight to the reminders service (ADR-6).
      var row = h("div", { class: "row" }, button("Done", "button", function () {
        act(reminders("/reminders/" + id + "/done", { method: "POST" }));
      }));
      // Only a due reminder can be snoozed; reminders refuses the rest with 409.
      if (item.status === "due") {
        append(row, button("Snooze 1 hour", "button", function () {
          act(reminders("/reminders/" + id + "/snooze", { method: "POST" }));
        }));
      }
      append(panel, row);
    }

    if (hasFolders()) {
      var checklist = h("div", { class: "checklist" });
      state.folders.forEach(function (entry) {
        var label = h("label", null,
          h("input", { name: "folders", type: "checkbox", value: entry.id, checked: item.folder_ids.indexOf(entry.id) >= 0 }),
          " " + entry.name);
        label.style.setProperty("--depth", entry.depth);
        append(checklist, label);
      });
      append(panel, [h("span", { class: "menu-label" }, "Folders"), h("form", {
        class: "stack",
        onsubmit: function (event) {
          event.preventDefault();
          var ids = Array.from(event.target.querySelectorAll("input:checked")).map(function (box) { return Number(box.value); });
          act(inbox("/api/items/" + id + "/folders", { method: "PUT", json: { folder_ids: ids } }));
        },
      }, checklist, h("button", { class: "button", type: "submit" }, "Save folders"))]);
    }

    var kind = h("select", { name: "kind", "aria-label": "Kind" });
    KINDS.forEach(function (value) {
      append(kind, h("option", { value: value, selected: item.kind === value }, value.charAt(0).toUpperCase() + value.slice(1)));
    });
    append(panel, h("details", null, h("summary", null, "Edit"), h("form", {
      class: "stack",
      onsubmit: function (event) {
        event.preventDefault();
        var form = event.target;
        act(inbox("/api/items/" + id, { method: "PUT", json: {
          body: form.body.value,
          kind: form.kind.value,
          remind_at: form.remind_at.value,
          repeat_daily: form.repeat_daily.checked,
          repeat_until: form.repeat_until.value,
        } }));
      },
    },
      h("textarea", { name: "body", required: true }, item.body),
      kind,
      h("input", { name: "remind_at", type: "datetime-local", value: item.remind_local || "", "aria-label": "Remind at" }),
      h("label", null, h("input", { name: "repeat_daily", type: "checkbox", checked: item.repeat_daily }), " Every day"),
      h("input", { name: "repeat_until", type: "date", value: item.repeat_until || "", "aria-label": "Repeat until" }),
      h("button", { class: "button primary", type: "submit" }, "Save edit"))));

    append(panel, button("Delete", "button danger wide", confirmThen("Delete this note?", function () {
      act(inbox("/api/items/" + id, { method: "DELETE" }));
    })));

    return menu(h("details", { class: "menu" },
      h("summary", { class: "icon-button", "aria-label": "More actions" }, icon("dots", 18)), panel));
  }

  // Only one "..." menu is open at a time, and a click outside closes it.
  function menu(details) {
    details.addEventListener("toggle", function () {
      if (!details.open) return;
      document.querySelectorAll("details.menu[open]").forEach(function (other) { if (other !== details) other.open = false; });
    });
    return details;
  }

  // <img> cannot send the token, so each image is fetched as a blob and
  // shown from a local object URL. They are kept for the session.
  function loadImage(id, picture, link) {
    var show = function (url) { picture.src = url; link.href = url; };
    if (imageUrls[id]) return show(imageUrls[id]);
    inbox("/api/items/" + id + "/image").then(function (blob) {
      imageUrls[id] = URL.createObjectURL(blob);
      show(imageUrls[id]);
    }).catch(function () { picture.alt = "The image could not be loaded."; });
  }

  // Like a chat, the feed opens at the newest line. Images load after that
  // and push the bottom down, so it stays pinned for a moment unless the
  // person starts scrolling.
  function stickToBottom() {
    var pinned = true;
    var unpin = function () { pinned = false; };
    ["wheel", "touchstart", "keydown"].forEach(function (name) {
      window.addEventListener(name, unpin, { once: true, passive: true });
    });
    var stick = function () { if (pinned) window.scrollTo(0, document.body.scrollHeight); };
    stick();
    if ("ResizeObserver" in window) {
      var watcher = new ResizeObserver(stick);
      watcher.observe(document.body);
      window.setTimeout(function () { watcher.disconnect(); }, 3000);
    }
  }

  // ---------- logged out ----------

  var registering = false;

  function showAuth(message) {
    $("#app").hidden = true;
    $("#auth").hidden = false;
    document.title = (registering ? "Create account" : "Log in") + " · Noteezer";
    var error = $("[data-auth-error]");
    error.textContent = message || "";
    error.hidden = !message;
  }

  function setAuthMode(register) {
    registering = register;
    var title = register ? "Create account" : "Log in";
    $("[data-auth-title]").textContent = title;
    $("[data-auth-submit]").textContent = title;
    $("[data-auth-switch]").textContent = register ? "Log in instead" : "Create an account";
    $("[data-auth-form] input[name=password]").autocomplete = register ? "new-password" : "current-password";
    showAuth(null);
  }

  $("[data-auth-switch]").addEventListener("click", function (event) {
    event.preventDefault();
    setAuthMode(!registering);
  });

  $("[data-auth-form]").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = event.target;
    inbox(registering ? "/api/register" : "/api/login", {
      method: "POST",
      json: { email: form.email.value, password: form.password.value, timezone: browserZone() },
    }).then(function (answer) {
      setToken(answer.token);
      form.password.value = "";
      start();
    }).catch(function (error) { showAuth(error.message); });
  });

  // ---------- logged in ----------

  function start() {
    $("#auth").hidden = true;
    $("#app").hidden = false;
    readLocation();
    // The account follows the browser's timezone, so "friday 3pm" and the
    // date picker are read in the person's time, not the server's.
    inbox("/api/me/timezone", { method: "PUT", json: { timezone: browserZone() } }).catch(function () {});
    takeShared().then(function () { return load({ toBottom: true }); });
  }

  // Android's share sheet posts to /share on this site. The service worker
  // keeps that post and opens /?shared=1; here it is sent on to inbox.
  function takeShared() {
    if (!("caches" in window) || new URLSearchParams(window.location.search).get("shared") !== "1") {
      return Promise.resolve();
    }
    return caches.open(SHARE_CACHE).then(function (cache) {
      return cache.match("/shared").then(function (kept) {
        if (!kept) return null;
        return kept.formData().then(function (data) {
          return inbox("/api/share", { method: "POST", body: data });
        }).then(function () { return cache.delete("/shared"); });
      });
    }).catch(function (error) {
      if (error && error.message) showError(error.message);
    }).then(function () {
      window.history.replaceState(null, "", "/?folder=unsorted");
    });
  }

  document.querySelectorAll("[data-icon]").forEach(function (element) {
    var parts = element.dataset.icon.split(":");
    element.appendChild(icon(parts[0], Number(parts[1]) || 16));
  });

  document.querySelectorAll("[data-open-sidebar]").forEach(function (element) {
    element.addEventListener("click", function () { document.body.classList.add("sidebar-open"); });
  });
  document.querySelectorAll("[data-close-sidebar]").forEach(function (element) {
    element.addEventListener("click", function () { document.body.classList.remove("sidebar-open"); });
  });

  menu($("[data-folder-menu]"));
  document.addEventListener("click", function (event) {
    document.querySelectorAll("details.menu[open]").forEach(function (details) {
      if (!details.contains(event.target)) details.open = false;
    });
  });

  window.addEventListener("popstate", function () {
    if (!memoryToken) return;
    readLocation();
    load({ toBottom: true });
  });

  $("[data-logout]").addEventListener("click", function () {
    // The token simply stays behind; it is only valid until it expires (ADR-6).
    setToken(null);
    Object.keys(imageUrls).forEach(function (id) { URL.revokeObjectURL(imageUrls[id]); });
    imageUrls = {};
    window.history.replaceState(null, "", "/");
    setAuthMode(false);
  });

  $("[data-search]").addEventListener("submit", function (event) {
    event.preventDefault();
    go(state.folder, event.target.q.value.trim());
  });

  $("[data-folder-form]").addEventListener("submit", function (event) {
    event.preventDefault();
    var form = event.target;
    var parentId = form.elements.namedItem("parent").value;
    inbox("/api/folders", { method: "POST", json: { name: form.elements.namedItem("name").value, parent_id: parentId ? Number(parentId) : null } })
      .then(function (created) {
        form.elements.namedItem("name").value = "";
        go(String(created.id), "");
      })
      .catch(function (error) { if (error.status !== 401) showError(error.message); });
  });

  $("[data-rename-form]").addEventListener("submit", function (event) {
    event.preventDefault();
    act(inbox("/api/folders/" + state.folder + "/name", { method: "PUT", json: { name: event.target.elements.namedItem("name").value } }));
  });

  $("[data-move-form]").addEventListener("submit", function (event) {
    event.preventDefault();
    var value = event.target.parent.value;
    act(inbox("/api/folders/" + state.folder + "/parent", { method: "PUT", json: { parent_id: value ? Number(value) : null } }));
  });

  $("[data-delete-folder]").addEventListener("click", confirmThen(
    "Delete this folder and its subfolders? The notes inside move to Unsorted.",
    function () {
      inbox("/api/folders/" + state.folder, { method: "DELETE" })
        .then(function () { go("", ""); })
        .catch(function (error) { if (error.status !== 401) showError(error.message); });
    }));

  // ---------- the composer ----------

  var composer = $("form[data-capture]");
  var text = composer.body;
  var file = composer.image;
  var attached = $("[data-attached]");

  composer.querySelector("[data-toggle-options]").addEventListener("click", function () {
    composer.querySelector(".options").toggleAttribute("hidden");
  });

  // The box grows with the text, up to 40% of the screen.
  var grow = function () {
    text.style.height = "auto";
    if (text.scrollHeight) text.style.height = Math.min(text.scrollHeight, window.innerHeight * 0.4) + "px";
  };
  text.addEventListener("input", grow);

  // On a laptop Enter sends and Shift+Enter starts a new line, like a chat.
  // On a phone Enter keeps making new lines and the arrow button sends.
  var finePointer = window.matchMedia("(pointer: fine)").matches;
  text.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && finePointer && !event.isComposing) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });

  var showFile = function () {
    var name = file.files.length ? file.files[0].name : "";
    attached.hidden = !name;
    $("[data-attached-name]").textContent = name;
  };
  file.addEventListener("change", showFile);
  $("[data-clear-file]").addEventListener("click", function () { file.value = ""; showFile(); });

  // A screenshot pasted into the text box is attached to the image field.
  composer.addEventListener("paste", function (event) {
    var files = event.clipboardData ? Array.from(event.clipboardData.files) : [];
    var picture = files.find(function (one) { return one.type.indexOf("image/") === 0; });
    if (!picture) return;
    var transfer = new DataTransfer();
    transfer.items.add(picture);
    file.files = transfer.files;
    showFile();
    event.preventDefault();
  });

  composer.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!text.value.trim() && !file.files.length) return;
    var data = new FormData(composer);
    // Unticked boxes are left out of a form, so both are always sent.
    data.set("repeat_daily", composer.repeat_daily.checked ? "true" : "false");
    data.set("ai_image", composer.ai_image.checked ? "true" : "false");
    if (!file.files.length) data.delete("image");
    var current = currentFolder();
    if (current) data.set("folder_id", current.id);
    var sendButton = composer.querySelector(".send");
    sendButton.disabled = true;
    inbox("/api/items", { method: "POST", body: data })
      .then(function () {
        composer.reset();
        composer.querySelector(".options").hidden = true;
        showFile();
        grow();
        state.polls = 0;
        return load({ toBottom: true });
      })
      .catch(function (error) { if (error.status !== 401) showError(error.message); })
      .then(function () { sendButton.disabled = false; text.focus(); });
  });

  // ---------- go ----------

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  // Wake both services as soon as the page opens, before anyone types a
  // password: logging in is a POST, which is not retried.
  function wakeUp() {
    [INBOX, REMINDERS].forEach(function (base) {
      sendWithRetries(base + "/health", { method: "GET" }).catch(function () {});
    });
  }

  if (!INBOX || !REMINDERS) {
    showAuth("config.js does not say where the services are.");
  } else if (memoryToken) {
    wakeUp();
    start();
  } else {
    wakeUp();
    setAuthMode(false);
  }
})();
