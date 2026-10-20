/* Steam report UI. The generator embeds the catalog in the page; this file
   renders it, filters AI disclosures, and talks to server.py for accounts. */
(function () {
  const pageSize = 30;
  const prefKey = "steam-report-prefs";
  const listLabels = { all: "全部", game: "普通游戏", demo: "Demo", potential: "潜力游戏" };
  const titleMap = { all: "全部新品", game: "普通游戏", demo: "Demo", potential: "潜力游戏" };

  const state = {
    kind: "all",
    paidOnly: false,
    hideAi: false,
    unseenOnly: false,
    sort: "release-desc",
    page: 1,
    loggedIn: false,
  };
  const auth = {
    user: null,
    allowRegister: true,
    seen: new Set(),
    positions: {},
  };
  let saveTimer = 0;

  function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function listLabel(kind) {
    return listLabels[kind] || "全部";
  }

  function filterItems(items, potentialItems, viewState, seen) {
    const source = viewState.kind === "potential" ? potentialItems : items;
    const seenSet = seen instanceof Set ? seen : new Set((seen || []).map(String));
    const hideSeen = !!viewState.unseenOnly && !!viewState.loggedIn;
    const collator = viewState.collator || new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
    return source.filter((item) => {
      if (!["all", "potential"].includes(viewState.kind) && item.kind !== viewState.kind) return false;
      if (viewState.paidOnly && item.is_free && item.kind !== "demo") return false;
      if (viewState.hideAi && item.ai_involved) return false;
      if (hideSeen && seenSet.has(String(item.appid))) return false;
      return true;
    }).sort((a, b) => {
      if (viewState.sort === "price-asc") return (a.price_value ?? 999999999) - (b.price_value ?? 999999999);
      if (viewState.sort === "price-desc") return (b.price_value ?? -1) - (a.price_value ?? -1);
      if (viewState.sort === "title-asc") return collator.compare(a.title || "", b.title || "");
      return String(b.release_date || "").localeCompare(String(a.release_date || ""))
        || (a.source_order ?? 999999999) - (b.source_order ?? 999999999);
    });
  }

  function pageOf(list, page, size) {
    const totalPages = Math.max(1, Math.ceil(list.length / size) || 1);
    const current = Math.min(Math.max(1, page || 1), totalPages);
    const start = (current - 1) * size;
    return { page: current, totalPages, items: list.slice(start, start + size) };
  }

  function selectBulkTargets(pageItems, seen, loggedIn) {
    const seenSet = seen instanceof Set ? seen : new Set((seen || []).map(String));
    if (!loggedIn) return { targets: pageItems.slice(), skipped: [] };
    const targets = [];
    const skipped = [];
    pageItems.forEach((item) => {
      if (seenSet.has(String(item.appid))) skipped.push(item);
      else targets.push(item);
    });
    return { targets, skipped };
  }

  function currentPayload() {
    return {
      kind: state.kind,
      page: state.page,
      paid_only: state.paidOnly,
      hide_ai: state.hideAi,
      unseen_only: state.unseenOnly,
      sort: state.sort,
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { filterItems, pageOf, selectBulkTargets, esc, aiBadge };
  }

  if (typeof document === "undefined") return;

  const app = document.getElementById("app");
  if (!app) return;
  const items = readJson("report-data");
  const potentialItems = readJson("potential-report-data");

  function readJson(id) {
    const node = document.getElementById(id);
    if (!node) return [];
    try {
      const parsed = JSON.parse(node.textContent || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  }

  function currentItems() {
    state.loggedIn = !!auth.user;
    return filterItems(items, potentialItems, state, auth.seen);
  }

  function currentPageItems(list = currentItems()) {
    const paged = pageOf(list, state.page, pageSize);
    state.page = paged.page;
    return paged.items;
  }

  function steamImageCandidates(src) {
    if (!src) return [];
    const candidates = [src];
    try {
      const url = new URL(src);
      const fallbackHosts = ["shared.fastly.steamstatic.com", "shared.akamai.steamstatic.com"];
      if (url.hostname.endsWith("steamstatic.com")) {
        fallbackHosts.forEach((host) => {
          if (host !== url.hostname) {
            const next = new URL(url.href);
            next.hostname = host;
            candidates.push(next.href);
          }
        });
      }
    } catch (error) {}
    return [...new Set(candidates)];
  }

  function imageAttributes(item) {
    const candidates = steamImageCandidates(item.header_image || item.image || "");
    return {
      src: candidates[0] || "",
      candidates: candidates.join("|"),
    };
  }

  function retryImage(img) {
    const candidates = (img.dataset.candidates || "").split("|").filter(Boolean);
    const nextIndex = Number(img.dataset.retryIndex || 0) + 1;
    if (nextIndex < candidates.length) {
      img.dataset.retryIndex = String(nextIndex);
      img.src = candidates[nextIndex];
      return;
    }
    const attempts = Number(img.dataset.attempts || 0);
    if (attempts < 2 && candidates[0]) {
      img.dataset.attempts = String(attempts + 1);
      img.dataset.retryIndex = "0";
      const separator = candidates[0].includes("?") ? "&" : "?";
      window.setTimeout(() => {
        img.src = `${candidates[0]}${separator}retry=${Date.now()}`;
      }, 500 * (attempts + 1));
      return;
    }
    img.classList.add("broken");
  }
  window.retryImage = retryImage;

  function itemUrl(item) {
    return item.steam_url || `https://store.steampowered.com/app/${item.appid}/`;
  }

  function aiBadge(item) {
    if (!item || !item.ai_involved) return "";
    const tip = String(item.ai_disclosure || "").trim() || "Steam 商店页标注了 AI 生成内容披露";
    return `<span class="badge ai" title="${esc(tip)}">AI</span>`;
  }

  function card(item) {
    const kindLabel = item.kind === "demo" ? "Demo" : "普通游戏";
    const genreChips = (item.genres && item.genres.length ? item.genres : item.tag_names || []).slice(0, 4);
    const desc = (item.short_description || "").trim();
    const descHtml = desc ? `<p class="desc">${esc(desc)}</p>` : "";
    const original = item.original_price_text && item.original_price_text !== item.price_text
      ? `<span class="original">${esc(item.original_price_text)}</span>` : "";
    const discount = item.discount_text ? `<span class="badge discount">${esc(item.discount_text)}</span>` : "";
    const review = item.review_count
      ? `<span class="chip review">${Number(item.review_count).toLocaleString("zh-CN")} 篇 · ${item.review_positive_percent ?? "?"}% 好评</span>`
      : "";
    const seen = !!(auth.user && auth.seen.has(String(item.appid)));
    const seenBadge = seen ? `<span class="badge seen">已看</span>` : "";
    const image = imageAttributes(item);
    return `
      <article class="card${seen ? " seen" : ""}" data-appid="${esc(item.appid)}">
        <div class="cover">
          <div class="placeholder"></div>
          <img src="${esc(image.src)}" data-candidates="${esc(image.candidates)}" data-retry-index="0" alt="${esc(item.title)} 封面" loading="lazy" onload="this.classList.remove('broken')" onerror="retryImage(this)">
          <div class="badge-row">
            <span class="badge ${item.kind === "demo" ? "demo" : ""}">${kindLabel}</span>
            ${aiBadge(item)}
            ${seenBadge}
            ${discount}
          </div>
        </div>
        <div class="content">
          <div class="title-row">
            <h3>${esc(item.title)}</h3>
            <div class="price">${esc(item.price_text || "价格未显示")}${original}</div>
          </div>
          ${descHtml}
          <div class="meta">
            <span class="chip">${esc(item.release_date || item.release_date_text || "日期未知")}</span>
            ${review}
            ${genreChips.map((value) => `<span class="chip">${esc(value)}</span>`).join("")}
          </div>
          <div class="footer">
            <button class="mark" type="button" data-seen-toggle="${esc(item.appid)}">${seen ? "标为未看" : "标为已看"}</button>
            <a class="open" href="${esc(itemUrl(item))}" target="_blank" rel="noreferrer">打开 Steam</a>
          </div>
        </div>
      </article>
    `;
  }

  function pageNumbers(total) {
    return Array.from({ length: total }, (_, index) => index + 1);
  }

  function renderPagination(totalItems, totalPages) {
    if (totalItems === 0) return "";
    const buttons = pageNumbers(totalPages).map((page) => (
      `<button class="page-btn ${page === state.page ? "active" : ""}" type="button" data-page="${page}">${page}</button>`
    )).join("");
    return `
      <div class="pagination">
        <div>第 ${state.page} / ${totalPages} 页，共 ${totalItems} 个结果</div>
        <div class="pages">
          <button class="page-btn" type="button" data-page="prev" ${state.page === 1 ? "disabled" : ""}>上一页</button>
          ${buttons}
          <button class="page-btn" type="button" data-page="next" ${state.page === totalPages ? "disabled" : ""}>下一页</button>
        </div>
      </div>
    `;
  }

  function render() {
    if (state.unseenOnly && !auth.user) {
      return `
        <section>
          <div class="section-title">
            <h2>${titleMap[state.kind]}</h2>
            <p>登录后即可只看未看的作品</p>
          </div>
          <div class="empty">登录后即可只看未看的作品。</div>
          <div class="credit">Made by Yling</div>
        </section>
      `;
    }
    const list = currentItems();
    const paged = pageOf(list, state.page, pageSize);
    state.page = paged.page;
    const body = paged.items.length
      ? `<div class="grid">${paged.items.map(card).join("")}</div>`
      : `<div class="empty">当前筛选下没有结果。</div>`;
    const seenOnPage = auth.user
      ? paged.items.filter((item) => auth.seen.has(String(item.appid))).length
      : 0;
    const seenNote = seenOnPage ? ` · 本页已看 ${seenOnPage}` : "";
    return `
      <section>
        <div class="section-title">
          <h2>${titleMap[state.kind]}</h2>
          <p>${list.length} 个结果 · 每页 ${pageSize} 个${seenNote}</p>
        </div>
        ${body}
        ${renderPagination(list.length, paged.totalPages)}
        <div class="credit">Made by Yling</div>
      </section>
    `;
  }

  function update() {
    app.innerHTML = render();
  }

  function syncToggles() {
    const mapping = [
      ["paidToggle", state.paidOnly],
      ["hideAiToggle", state.hideAi],
      ["unseenToggle", state.unseenOnly],
    ];
    mapping.forEach(([id, active]) => {
      const button = document.getElementById(id);
      if (!button) return;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const sort = document.getElementById("sort");
    if (sort) sort.value = state.sort;
    const button = document.getElementById("continueBtn");
    if (!button) return;
    const last = auth.positions && auth.positions.__last__;
    if (auth.user && last && last.kind && last.page) {
      button.textContent = `从上次继续（${listLabel(last.kind)} 第 ${last.page} 页）`;
    } else {
      button.textContent = "从上次继续";
    }
  }

  function savePrefs() {
    try {
      localStorage.setItem(prefKey, JSON.stringify({
        hideAi: state.hideAi,
        paidOnly: state.paidOnly,
        unseenOnly: state.unseenOnly,
        sort: state.sort,
      }));
    } catch (error) {}
  }

  function restorePrefs() {
    try {
      const prefs = JSON.parse(localStorage.getItem(prefKey) || "{}");
      if (typeof prefs.hideAi === "boolean") state.hideAi = prefs.hideAi;
      if (typeof prefs.paidOnly === "boolean") state.paidOnly = prefs.paidOnly;
      if (typeof prefs.unseenOnly === "boolean") state.unseenOnly = prefs.unseenOnly;
      if (typeof prefs.sort === "string") state.sort = prefs.sort;
    } catch (error) {}
  }

  function showAccountHint(text) {
    const msg = document.getElementById("accountMsg");
    if (msg) {
      msg.textContent = text;
      return;
    }
    const bar = document.getElementById("accountBar");
    if (!bar) return;
    let hint = document.getElementById("accountHint");
    if (!hint) {
      hint = document.createElement("p");
      hint.id = "accountHint";
      hint.className = "account-msg";
      bar.appendChild(hint);
    }
    hint.textContent = text;
  }

  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        ...options,
      });
    } catch (error) {
      throw new Error("登录服务不可用，请确认页面由报告服务打开。");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || "请求失败");
    }
    return data;
  }

  function renderAccount() {
    const bar = document.getElementById("accountBar");
    if (!bar) return;
    if (auth.user) {
      bar.innerHTML = `
        <span class="account-user">已登录 <strong>${esc(auth.user.username)}</strong></span>
        <button id="logoutBtn" class="toggle" type="button">退出</button>
        <p class="account-msg" id="accountMsg"></p>
      `;
      document.getElementById("logoutBtn").addEventListener("click", logout);
      return;
    }
    const registerButton = auth.allowRegister
      ? `<button id="registerBtn" class="toggle" type="button">注册</button>`
      : "";
    bar.innerHTML = `
      <form id="loginForm">
        <input id="username" name="username" autocomplete="username" placeholder="用户名" maxlength="32" required>
        <input id="password" name="password" type="password" autocomplete="current-password" placeholder="密码" maxlength="128" required>
        <button class="toggle" type="submit">登录</button>
        ${registerButton}
      </form>
      <p class="account-msg" id="accountMsg"></p>
    `;
    document.getElementById("loginForm").addEventListener("submit", (event) => {
      event.preventDefault();
      submitAuth("login");
    });
    const register = document.getElementById("registerBtn");
    if (register) register.addEventListener("click", () => submitAuth("register"));
  }

  async function loadSession() {
    try {
      const me = await api("/api/me");
      auth.user = me.user || null;
      auth.allowRegister = me.allow_register !== false;
      auth.seen = new Set((me.seen || []).map(String));
      auth.positions = me.positions || {};
    } catch (error) {
      if (!auth.user) auth.allowRegister = true;
    }
    renderAccount();
  }

  async function submitAuth(kind) {
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    const msg = document.getElementById("accountMsg");
    if (msg) msg.textContent = "";
    try {
      await api(kind === "register" ? "/api/register" : "/api/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      await loadSession();
      syncToggles();
      update();
    } catch (error) {
      const current = document.getElementById("accountMsg");
      if (current) current.textContent = error.message || "登录失败";
    }
  }

  async function logout() {
    try {
      await api("/api/logout", { method: "POST", body: "{}" });
    } catch (error) {}
    auth.user = null;
    auth.seen = new Set();
    auth.positions = {};
    renderAccount();
    syncToggles();
    update();
  }

  async function setSeen(appids, seen) {
    const ids = [...new Set(appids.map((id) => String(id)).filter(Boolean))];
    if (!ids.length) return false;
    if (!auth.user) {
      showAccountHint("登录后才会记住已看记录");
      const username = document.getElementById("username");
      if (username) username.focus();
      return false;
    }
    const previous = new Set(auth.seen);
    ids.forEach((id) => {
      if (seen) auth.seen.add(id);
      else auth.seen.delete(id);
    });
    update();
    try {
      await api("/api/seen", { method: "POST", body: JSON.stringify({ appids: ids, seen: !!seen }) });
      return true;
    } catch (error) {
      auth.seen = previous;
      update();
      showAccountHint(error.message || "保存已看记录失败");
      return false;
    }
  }

  function markPosition() {
    savePrefs();
    if (!auth.user) return;
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      const payload = currentPayload();
      api("/api/position", { method: "POST", body: JSON.stringify(payload) })
        .then(() => {
          auth.positions[payload.kind] = payload;
          auth.positions.__last__ = payload;
          syncToggles();
        })
        .catch(() => {});
    }, 350);
  }

  function flashContinue(text) {
    const button = document.getElementById("continueBtn");
    if (!button) return;
    const previous = button.textContent;
    button.textContent = text;
    window.setTimeout(() => { button.textContent = previous; }, 1600);
  }

  function continueLast() {
    if (!auth.user) {
      showAccountHint("登录后可从上次继续");
      const username = document.getElementById("username");
      if (username) username.focus();
      return;
    }
    const last = auth.positions && auth.positions.__last__;
    if (!last || !last.kind) {
      flashContinue("还没有上次的位置");
      return;
    }
    state.kind = last.kind;
    state.page = Number(last.page) || 1;
    state.paidOnly = !!last.paid_only;
    state.hideAi = !!last.hide_ai;
    state.unseenOnly = !!last.unseen_only;
    state.sort = last.sort || "release-desc";
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.kind === state.kind);
    });
    savePrefs();
    syncToggles();
    update();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function openCurrentList() {
    const button = document.getElementById("bulkOpen");
    const { targets, skipped } = selectBulkTargets(currentPageItems(), auth.seen, !!auth.user);
    const urls = targets
      .map((item) => ({ url: itemUrl(item), appid: String(item.appid) }))
      .filter((item) => item.url);
    if (!urls.length) {
      button.textContent = skipped.length ? "本页已全部看过" : "没有可打开的游戏";
      window.setTimeout(() => { button.textContent = "打开本页游戏"; }, 1600);
      return;
    }
    const delayMs = 100;
    const skipNote = skipped.length ? `，跳过已看 ${skipped.length} 个` : "";
    button.disabled = true;
    button.textContent = `准备打开 ${urls.length} 个${skipNote}...`;
    const opened = [];
    let blocked = false;
    urls.forEach((item, index) => {
      window.setTimeout(() => {
        const popup = window.open(item.url, "_blank");
        if (popup) {
          popup.opener = null;
          opened.push(item.appid);
        } else {
          blocked = true;
        }
        button.textContent = `已打开 ${opened.length} / ${urls.length}${skipNote}`;
        if (index !== urls.length - 1) return;
        if (opened.length) setSeen(opened, true);
        window.setTimeout(() => {
          button.disabled = false;
          button.textContent = blocked ? "浏览器拦截了部分弹窗" : "打开本页游戏";
          if (blocked) {
            window.setTimeout(() => { button.textContent = "打开本页游戏"; }, 1800);
          }
        }, 900);
      }, index * delayMs);
    });
  }

  document.getElementById("bulkOpen").addEventListener("click", openCurrentList);
  document.getElementById("continueBtn").addEventListener("click", continueLast);
  document.getElementById("sort").addEventListener("change", (event) => {
    state.sort = event.target.value;
    state.page = 1;
    markPosition();
    update();
  });
  document.getElementById("paidToggle").addEventListener("click", () => {
    state.paidOnly = !state.paidOnly;
    state.page = 1;
    syncToggles();
    markPosition();
    update();
  });
  document.getElementById("hideAiToggle").addEventListener("click", () => {
    state.hideAi = !state.hideAi;
    state.page = 1;
    syncToggles();
    markPosition();
    update();
  });
  document.getElementById("unseenToggle").addEventListener("click", () => {
    state.unseenOnly = !state.unseenOnly;
    state.page = 1;
    syncToggles();
    markPosition();
    update();
  });
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      if (auth.user) auth.positions[state.kind] = currentPayload();
      state.kind = button.dataset.kind;
      document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab === button));
      const saved = auth.positions[state.kind];
      state.page = saved && Number(saved.page) ? Number(saved.page) : 1;
      markPosition();
      update();
    });
  });
  app.addEventListener("click", (event) => {
    const seenToggle = event.target.closest("[data-seen-toggle]");
    if (seenToggle) {
      const appid = seenToggle.getAttribute("data-seen-toggle");
      setSeen([appid], !auth.seen.has(String(appid)));
      return;
    }
    const openLink = event.target.closest("a.open");
    if (openLink) {
      const card = openLink.closest(".card");
      const appid = card && card.dataset.appid;
      if (appid && auth.user && !auth.seen.has(String(appid))) setSeen([appid], true);
    }
    const button = event.target.closest("[data-page]");
    if (!button || button.disabled) return;
    const list = currentItems();
    const totalPages = Math.max(1, Math.ceil(list.length / pageSize));
    const target = button.dataset.page;
    if (target === "prev") state.page = Math.max(1, state.page - 1);
    else if (target === "next") state.page = Math.min(totalPages, state.page + 1);
    else state.page = Number(target);
    markPosition();
    update();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  restorePrefs();
  syncToggles();
  update();
  loadSession().then(() => {
    syncToggles();
    update();
  });
})();
