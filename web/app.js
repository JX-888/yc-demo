const icons = {
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4"></path><path d="m7 9 5-5 5 5"></path><path d="M20 16v4H4v-4"></path></svg>',
  activity: '<svg viewBox="0 0 24 24"><path d="M3 12h4l3-8 4 16 3-8h4"></path></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>',
  spark: '<svg viewBox="0 0 24 24"><path d="M13 2 9 13l-7 2 7 2 4 5 2-7 7-2-7-2-2-9Z"></path></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>',
  image: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"></rect><circle cx="8.5" cy="10" r="1.5"></circle><path d="m21 15-5-5L5 19"></path></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"></rect><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"></path></svg>',
};

let materials = [];
let selectedTags = new Set();
const hiddenDisplayTags = new Set(["待人工复核", "微信对话"]);
const imageTypeOptions = new Set([
  "好评",
  "异议",
  "学生没钱/跟家长沟通",
  "教育理念/老师推荐图",
  "报过辅导班对比图",
  "竞品对比",
  "平板",
  "成交确认",
  "其他",
]);
const semanticIntents = [
  {
    key: "scoreResult",
    terms: ["中考", "录取", "上岸", "重高", "查分", "成绩", "分数", "提分", "涨分", "进步", "提升", "排名", "超预期", "比预估高", "考上"],
  },
  {
    key: "valueObjection",
    terms: ["价格", "太贵", "贵", "值不值", "划算", "物超所值", "五千", "5000", "投入", "费用", "预算", "异议"],
  },
  {
    key: "trustPraise",
    terms: ["谢谢", "感谢", "认可", "好评", "真实反馈", "效果好", "课程认可", "信任", "靠谱", "有帮助"],
  },
  {
    key: "activeLearning",
    terms: ["主动", "自觉", "自律", "坚持", "每天学", "愿意学", "学习习惯", "不催", "动力"],
  },
  {
    key: "gapFilling",
    terms: ["查漏补缺", "补漏", "补基础", "基础差", "薄弱点", "知识点", "听不懂", "不会", "断层"],
  },
  {
    key: "referral",
    terms: ["转介绍", "推荐朋友", "推荐给朋友", "朋友推荐", "身边朋友", "班级群", "老带新"],
  },
  {
    key: "competitor",
    terms: ["竞品", "对比", "辅导班", "补习班", "线下班", "一对一", "其他机构", "报过班"],
  },
  {
    key: "payment",
    terms: ["成交", "付款", "报名", "转账", "收款", "支付", "订单"],
  },
  {
    key: "parentCommunication",
    terms: ["家长沟通", "跟家长说", "没钱", "学生没钱", "问家长", "父母", "妈妈", "爸爸"],
  },
];
const apiOrigin =
  window.location.protocol === "file:" || window.location.port === "8765" ? "http://127.0.0.1:8787" : "";
let uploadedQueue = [];

const els = {
  pageTitle: document.querySelector("#pageTitle"),
  pageSubtitle: document.querySelector("#pageSubtitle"),
  navItems: document.querySelectorAll(".nav-item"),
  views: document.querySelectorAll(".view"),
  searchInput: document.querySelector("#searchInput"),
  stageFilter: document.querySelector("#stageFilter"),
  gradeFilter: document.querySelector("#gradeFilter"),
  imageTypeFilter: document.querySelector("#imageTypeFilter"),
  sortSelect: document.querySelector("#sortSelect"),
  tagFilters: document.querySelector("#tagFilters"),
  resultsGrid: document.querySelector("#resultsGrid"),
  resultCount: document.querySelector("#resultCount"),
  clearFiltersBtn: document.querySelector("#clearFiltersBtn"),
  uploadForm: document.querySelector("#uploadForm"),
  imageInput: document.querySelector("#imageInput"),
  previewStrip: document.querySelector("#previewStrip"),
  dropZone: document.querySelector("#dropZone"),
  manualTagInput: document.querySelector("#manualTagInput"),
  uploadStage: document.querySelector("#uploadStage"),
  uploadGrade: document.querySelector("#uploadGrade"),
  uploadImageType: document.querySelector("#uploadImageType"),
  noteInput: document.querySelector("#noteInput"),
  queueRows: document.querySelector("#queueRows"),
  queueRunBtn: document.querySelector("#queueRunBtn"),
  simulateAnalysisBtn: document.querySelector("#simulateAnalysisBtn"),
  sidebarTotal: document.querySelector("#sidebarTotal"),
  sidebarPending: document.querySelector("#sidebarPending"),
  toast: document.querySelector("#toast"),
};

document.querySelectorAll("[data-icon]").forEach((node) => {
  node.innerHTML = icons[node.dataset.icon] || "";
});

async function init() {
  await loadMaterials();

  renderTagFilters();
  renderResults();
  renderQueue();
  updateStats();
}

async function loadMaterials() {
  try {
    const response = await fetch(`${apiOrigin}/api/materials?v=${Date.now()}`);
    const data = await response.json();
    materials = data.map((item, index) => ({
      ...item,
      imageType: normalizeImageType(item.imageType),
      pitch: item.pitch || "",
      uploadedAt: Date.now() - index * 86400000,
      used: Math.max(0, 18 - index * 2),
      status: item.status || "已完成",
    }));
    syncQueueFromMaterials();
  } catch (error) {
    showToast("演示素材加载失败");
  }
}

function normalizeImageType(value) {
  const text = String(value || "").trim();
  if (!text) return "其他";
  return imageTypeOptions.has(text) ? text : text;
}

function syncQueueFromMaterials() {
  uploadedQueue = materials
    .filter((item) => item.status && item.status !== "已完成")
    .map(materialToQueueRow);
}

function materialToQueueRow(item) {
  return {
    id: item.id,
    title: item.title || "待分析素材",
    manualTag: item.manualTag || "未填写人工标签",
    status: item.status || "待分析",
    createdAt: "飞书同步",
  };
}

function switchView(viewName) {
  els.navItems.forEach((item) => item.classList.toggle("active", item.dataset.view === viewName));
  els.views.forEach((view) => view.classList.toggle("active", view.id === `${viewName}View`));

  const titles = {
    search: ["素材搜索", "输入需求或选择标签，快速找到可复用的微信截图素材。"],
    upload: ["上传入库", "新增图片先进入飞书多维表，后台再批量分析。"],
    queue: ["分析队列", "查看待分析、分析中和已完成的素材状态。"],
  };
  els.pageTitle.textContent = titles[viewName][0];
  els.pageSubtitle.textContent = titles[viewName][1];
}

function getAllTags() {
  const tagCount = new Map();
  materials.forEach((item) => {
    [item.manualTag, ...(item.aiTags || [])].forEach((tag) => {
      if (!tag) return;
      const shortTag = String(tag).split(/\s|，|,|\n/).filter(Boolean)[0];
      if (!shortTag) return;
      if (hiddenDisplayTags.has(shortTag)) return;
      tagCount.set(shortTag, (tagCount.get(shortTag) || 0) + 1);
    });
  });
  return [...tagCount.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 14)
    .map(([tag]) => tag);
}

function renderTagFilters() {
  const tags = getAllTags();
  els.tagFilters.innerHTML = tags
    .map(
      (tag) =>
        `<button class="chip ${selectedTags.has(tag) ? "active" : ""}" type="button" data-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`,
    )
    .join("");

  els.tagFilters.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const tag = chip.dataset.tag;
      if (selectedTags.has(tag)) selectedTags.delete(tag);
      else selectedTags.add(tag);
      chip.classList.toggle("active", selectedTags.has(tag));
      renderResults();
    });
  });
}

function scoreMaterial(item, query) {
  const q = normalizeSearchText(query);
  const corpus = buildSearchCorpus(item);
  const queryTerms = expandQueryTerms(q);
  let score = 0;

  if (!q && !selectedTags.size) score += 1;
  if (q) {
    score += scoreLexicalMatch(corpus, q, queryTerms);
    score += scoreSemanticIntent(corpus.all, q);
    score += scoreBusinessFit(item, q);
    if (q.includes("中考") && !containsSearchTerm(corpus.all, "中考")) score -= 90;
  }

  selectedTags.forEach((tag) => {
    if (getTagPool(item).includes(String(tag).toLowerCase())) score += 45;
  });

  return score;
}

function buildSearchCorpus(item) {
  const fields = {
    focus: item.focus || "",
    manualTag: item.manualTag || "",
    pitch: item.pitch || "",
    evidence: item.evidence || "",
    imageType: item.imageType || "",
    aiTags: (item.aiTags || []).join(" "),
    keywords: (item.keywords || []).join(" "),
    title: item.title || "",
    summary: item.summary || "",
    scenario: item.scenario || "",
    searchText: item.searchText || "",
  };
  fields.all = normalizeSearchText(Object.values(fields).join("\n"));
  return fields;
}

function scoreLexicalMatch(corpus, query, queryTerms) {
  let score = 0;
  const weightedFields = [
    [corpus.manualTag, 95],
    [corpus.focus, 75],
    [corpus.pitch, 55],
    [corpus.evidence, 44],
    [corpus.imageType, 40],
    [corpus.aiTags, 36],
    [corpus.keywords, 32],
    [corpus.scenario, 26],
    [corpus.title, 22],
    [corpus.summary, 16],
    [corpus.searchText, 6],
  ];

  weightedFields.forEach(([text, weight]) => {
    if (!query) return;
    if (containsSearchTerm(text, query)) score += weight;
    queryTerms.forEach((part) => {
      if (part !== query && containsSearchTerm(text, part)) {
        score += Math.max(2, Math.round(weight / 4));
      }
    });
  });
  return score;
}

function scoreSemanticIntent(text, query) {
  const queryVector = buildIntentVector(query);
  const textVector = buildIntentVector(text);
  const similarity = cosineSimilarity(queryVector, textVector);
  return Math.round(similarity * 92);
}

function scoreBusinessFit(item, query) {
  let score = 0;
  const imageType = item.imageType || "";
  if (containsSearchTerm(query, "价格") || containsSearchTerm(query, "太贵") || containsSearchTerm(query, "值不值")) {
    if (["异议", "好评"].includes(imageType)) score += 28;
  }
  if (containsSearchTerm(query, "竞品") || containsSearchTerm(query, "对比") || containsSearchTerm(query, "辅导班")) {
    if (["竞品对比", "报过辅导班对比图"].includes(imageType)) score += 34;
  }
  if (containsSearchTerm(query, "成交") || containsSearchTerm(query, "报名") || containsSearchTerm(query, "付款")) {
    if (imageType === "成交确认") score += 34;
  }
  if (containsSearchTerm(query, "家长") && imageType === "学生没钱/跟家长沟通") score += 26;
  return score;
}

function buildIntentVector(text) {
  const normalized = normalizeSearchText(text);
  return semanticIntents.map((intent) =>
    intent.terms.reduce((sum, term) => {
      if (!containsSearchTerm(normalized, term)) return sum;
      return sum + Math.min(3, Math.max(1, term.length - 1));
    }, 0),
  );
}

function cosineSimilarity(a, b) {
  const dot = a.reduce((sum, value, index) => sum + value * b[index], 0);
  const magA = Math.sqrt(a.reduce((sum, value) => sum + value * value, 0));
  const magB = Math.sqrt(b.reduce((sum, value) => sum + value * value, 0));
  if (!magA || !magB) return 0;
  return dot / (magA * magB);
}

function normalizeSearchText(value) {
  return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
}

function containsSearchTerm(text, term) {
  const normalizedText = normalizeSearchText(text);
  const normalizedTerm = normalizeSearchText(term);
  if (!normalizedText || !normalizedTerm) return false;
  if (normalizedTerm === "中考") return /(^|[^期])中考/.test(normalizedText);
  return normalizedText.includes(normalizedTerm);
}

function getTagPool(item) {
  return [
    item.manualTag,
    ...(item.aiTags || []),
    ...(item.keywords || []),
    item.imageType,
    item.pitch,
    item.summary,
    item.scenario,
    item.evidence,
    item.searchText,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function matchesSelectedTags(item) {
  if (!selectedTags.size) return true;
  const pool = getTagPool(item);
  return [...selectedTags].every((tag) => pool.includes(String(tag).toLowerCase()));
}

function expandQueryTerms(query) {
  if (!query) return [];
  const terms = new Set(query.split(/\s+/).filter(Boolean));
  const expansions = [
    [["价格贵", "太贵", "贵", "值不值", "划算"], ["物超所值", "5000", "五千", "值得", "价格认可", "处理价格异议", "值"]],
    [["推荐朋友", "推荐给朋友", "朋友推荐", "介绍朋友", "转介绍"], ["身边朋友", "推荐", "转介绍", "老带新", "班级群", "多推荐"]],
    [["孩子没动力", "不主动", "不自觉"], ["主动学习", "自觉", "自律", "每天都", "学习习惯", "爱学习"]],
    [["提分", "涨分", "分数提高"], ["成绩提升", "进步", "提高", "高了", "超预期"]],
    [["中考"], ["中考成绩", "查分", "录取", "重高", "考上"]],
    [["考上", "录取", "上岸"], ["中考", "高中录取", "提前录取", "重高", "录取通知书"]],
    [["补基础", "基础差", "听不懂"], ["查漏补缺", "补漏", "知识点", "听得懂", "补基础"]],
    [["报过班", "补课", "线下课"], ["辅导班", "补习班", "一对一", "竞品对比", "对比"]],
    [["家长不同意", "家长沟通", "没钱"], ["学生没钱", "问家长", "父母", "妈妈", "爸爸"]],
  ];

  expansions.forEach(([triggers, related]) => {
    if (triggers.some((trigger) => containsSearchTerm(query, trigger))) {
      related.forEach((term) => terms.add(term.toLowerCase()));
    }
  });

  query
    .split(/[\s，,。！？!、]+/)
    .filter(Boolean)
    .forEach((term) => terms.add(term.toLowerCase()));

  return [...terms];
}

function getFilteredResults() {
  const query = els.searchInput.value;
  const stage = els.stageFilter.value;
  const grade = els.gradeFilter.value;
  const imageType = els.imageTypeFilter.value;
  const sort = els.sortSelect.value;

  let rows = materials
    .map((item) => ({ ...item, score: scoreMaterial(item, query) }))
    .filter((item) => {
      if (stage && item.stage !== stage) return false;
      if (grade && item.grade !== grade) return false;
      if (imageType && item.imageType !== imageType) return false;
      if (violatesProtectedTerm(item, query)) return false;
      if (!matchesSelectedTags(item)) return false;
      if (query.trim()) return item.score > 0;
      return true;
    });

  if (sort === "latest") rows.sort((a, b) => b.uploadedAt - a.uploadedAt);
  else if (sort === "used") rows.sort((a, b) => b.used - a.used);
  else rows.sort((a, b) => b.score - a.score || b.used - a.used);

  return rows;
}

function violatesProtectedTerm(item, query) {
  const q = normalizeSearchText(query);
  if (!q) return false;
  const corpus = buildSearchCorpus(item).all;
  if (containsSearchTerm(q, "中考") && !containsSearchTerm(corpus, "中考")) return true;
  if (containsSearchTerm(q, "期中考") && !containsSearchTerm(corpus, "期中考")) return true;
  return false;
}

function renderResults() {
  const rows = getFilteredResults();
  els.resultCount.textContent = rows.length;
  els.resultsGrid.innerHTML = rows.map(renderMaterialCard).join("");

  els.resultsGrid.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const material = materials.find((item) => item.id === button.dataset.copy);
      if (!material) return;
      if (material.images.length > 1) await copyAllImages(material);
      else await copyImage(material.images[0], material);
    });
  });

  els.resultsGrid.querySelectorAll("[data-copy-image]").forEach((button) => {
    button.addEventListener("click", async () => {
      const material = materials.find((item) => item.id === button.dataset.copyImage);
      if (!material) return;
      const imageIndex = Number(button.dataset.imageIndex || 0);
      await copyImage(material.images[imageIndex], material, imageIndex + 1);
    });
  });

  els.resultsGrid.querySelectorAll("[data-copy-pitch]").forEach((button) => {
    button.addEventListener("click", async () => {
      const material = materials.find((item) => item.id === button.dataset.copyPitch);
      if (!material) return;
      await copyPitch(material);
    });
  });

  els.resultsGrid.querySelectorAll("[data-copy-package]").forEach((button) => {
    button.addEventListener("click", async () => {
      const material = materials.find((item) => item.id === button.dataset.copyPackage);
      if (!material) return;
      await copyMaterialPackage(material);
    });
  });
}

function renderMaterialCard(item) {
  const imageCount = item.images.length;
  const tags = (item.aiTags || []).filter((tag) => !hiddenDisplayTags.has(tag)).slice(0, 5);
  const stageLine = [item.stage, item.grade].filter(Boolean).join(" · ") || "未标注";
  const scenarioText = item.scenario || "未填写适用场景";
  const pitchText = item.pitch || "暂无推荐话术，完成分析后会自动生成。";
  const imageStageClass = imageCount > 1 ? "image-stage multi" : "image-stage";
  return `
    <article class="material-card">
      <div class="${imageStageClass}">
        <div class="image-track">
          ${item.images
            .map(
              (src, index) => `
                <div class="image-tile">
                  <img loading="lazy" src="${escapeHtml(src)}" alt="${escapeHtml(item.title)} 第 ${index + 1} 张" />
                  ${
                    imageCount > 1
                      ? `<button class="copy-single" type="button" data-copy-image="${escapeHtml(item.id)}" data-image-index="${index}">
                          <span class="icon">${icons.copy}</span>
                          <span>复制此图</span>
                        </button>`
                      : ""
                  }
                </div>
              `,
            )
            .join("")}
        </div>
        ${imageCount > 1 ? `<span class="image-count">${imageCount} 张</span>` : ""}
        <button class="copy-float" type="button" data-copy="${escapeHtml(item.id)}" title="${imageCount > 1 ? "复制全部图片" : "复制图片"}">
          <span class="icon">${icons.copy}</span>
          <span>${imageCount > 1 ? "复制全部" : "复制图片"}</span>
        </button>
      </div>
      <div class="material-body">
        <div class="manual-tag">${escapeHtml(item.manualTag || "未填写人工标签")}</div>
        <div class="meta-row">
          <span class="pill blue">${escapeHtml(stageLine)}</span>
          <span class="pill type">${escapeHtml(item.imageType || "其他")}</span>
          ${tags.map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`).join("")}
        </div>
        <div class="scenario-text">${escapeHtml(scenarioText)}</div>
        <div class="pitch-box">${escapeHtml(pitchText)}</div>
        <div class="card-actions">
          <button class="ghost-button small" type="button" data-copy-pitch="${escapeHtml(item.id)}">
            <span class="icon">${icons.copy}</span>
            <span>复制话术</span>
          </button>
          <button class="ghost-button small" type="button" data-copy-package="${escapeHtml(item.id)}">
            <span class="icon">${icons.copy}</span>
            <span>复制图文</span>
          </button>
        </div>
        <div class="match-row">相关分 ${Math.max(0, item.score || 0)} · 使用 ${item.used || 0} 次</div>
      </div>
    </article>
  `;
}

async function copyImage(src, material, imageNumber = 1) {
  try {
    if (supportsImageClipboard()) {
      const blob = await createSingleImageBlob(src);
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      showToast(`第 ${imageNumber} 张图片已复制，可以粘贴到企业微信`);
      return;
    }
    await copyMaterialText(material);
    showToast(clipboardLimitMessage("已复制素材文字"));
  } catch (error) {
    showToast(clipboardLimitMessage("复制受浏览器限制"));
  }
}

async function copyAllImages(material) {
  try {
    if (!supportsImageClipboard()) {
      await copyMaterialText(material);
      showToast(clipboardLimitMessage("当前浏览器不支持批量复制图片"));
      return;
    }

    const blob = await createHorizontalCompositeBlob(material.images);
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
    showToast(`已横向拼接 ${material.images.length} 张图片并复制`);
  } catch (error) {
    showToast(clipboardLimitMessage("浏览器限制批量复制"));
  }
}

async function copyPitch(material) {
  try {
    if (!navigator.clipboard?.writeText || !window.isSecureContext) {
      showToast(clipboardLimitMessage("复制话术受浏览器限制"));
      return;
    }
    await navigator.clipboard.writeText(buildShareText(material));
    showToast("推荐话术已复制");
  } catch (error) {
    showToast("复制话术失败，请手动选择复制");
  }
}

async function copyMaterialPackage(material) {
  const shareText = buildShareText(material);
  try {
    if (!supportsRichClipboard()) {
      if (await copyMaterialPackageSelection(material, shareText)) {
        showToast(material.images.length > 1 ? "已复制图文（含全部图片）" : "已复制图文");
      } else {
        await copyPitch(material);
      }
      return;
    }

    const shareHtml = await buildShareHtml(material, shareText);
    await navigator.clipboard.write([
      new ClipboardItem({
        "text/html": new Blob([shareHtml], { type: "text/html" }),
        "text/plain": new Blob([shareText], { type: "text/plain" }),
      }),
    ]);
    showToast(material.images.length > 1 ? "已复制图文（含全部图片）" : "已复制图文");
  } catch (error) {
    if (await copyMaterialPackageSelection(material, shareText)) {
      showToast(material.images.length > 1 ? "已复制图文（含全部图片）" : "已复制图文");
    } else {
      try {
        await navigator.clipboard.writeText(shareText);
        showToast("图文复制受限，已复制话术");
      } catch {
        showToast(clipboardLimitMessage("图文复制受浏览器限制"));
      }
    }
  }
}

async function copyMaterialPackageSelection(material, shareText) {
  const selection = window.getSelection?.();
  if (!selection || typeof document.execCommand !== "function") return false;

  const container = document.createElement("div");
  container.setAttribute("contenteditable", "true");
  container.style.position = "fixed";
  container.style.left = "-10000px";
  container.style.top = "0";
  container.style.width = "720px";
  container.style.padding = "16px";
  container.style.background = "#ffffff";
  container.innerHTML = buildShareFragmentHtml(material, shareText);
  document.body.appendChild(container);

  try {
    await waitForClipboardImages(container.querySelectorAll("img"));
    const range = document.createRange();
    range.selectNodeContents(container);
    selection.removeAllRanges();
    selection.addRange(range);
    return document.execCommand("copy");
  } catch (error) {
    return false;
  } finally {
    selection.removeAllRanges();
    container.remove();
  }
}

function supportsRichClipboard() {
  return Boolean(navigator.clipboard?.write && window.ClipboardItem && window.isSecureContext);
}

function supportsImageClipboard() {
  return Boolean(navigator.clipboard?.write && window.ClipboardItem && window.isSecureContext);
}

async function copyMaterialText(material) {
  if (!navigator.clipboard?.writeText || !window.isSecureContext) return;
  await navigator.clipboard.writeText(buildShareText(material));
}

function buildShareText(material) {
  const pitch = (material.pitch || "").trim();
  if (pitch) return normalizeShareText(pitch);
  if (material.manualTag) return normalizeShareText(`您可以看下这个真实反馈，${material.manualTag}`);
  if (material.scenario) return normalizeShareText(`您可以参考一下这个案例，和孩子现在的情况比较接近。`);
  return "您可以先看下这个真实反馈。";
}

function normalizeShareText(text) {
  return String(text || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .join("")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}


function clipboardLimitMessage(prefix) {
  const addressHint = window.location.protocol === "http:" && !["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "，局域网 HTTP 地址不支持图片复制，请改用 HTTPS"
    : "，请右键图片复制";
  return `${prefix}${addressHint}`;
}

async function buildShareHtml(material, shareText) {
  const fragmentHtml = buildShareFragmentHtml(material, shareText);
  return `
    <html>
      <head><meta charset="utf-8" /></head>
      <body>${fragmentHtml}</body>
    </html>
  `;
}

function buildShareFragmentHtml(material, shareText) {
  const paragraphs = String(shareText || "")
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => `<p style="margin:0 0 10px;line-height:1.7;">${escapeHtml(line)}</p>`)
    .join("");
  const imageHtml = (material.images || [])
    .map((src, index) => {
      const imageSrc = shareImageUrl(src);
      const safeImageSrc = escapeHtml(imageSrc);
      return `<img src="${safeImageSrc}" data-src="${safeImageSrc}" _src="${safeImageSrc}" data-original="${safeImageSrc}" alt="${escapeHtml(material.title || "素材图片")} ${index + 1}" style="display:block;max-width:720px;width:100%;height:auto;margin:10px 0;border:0;" />`;
    })
    .join("");

  return `
    <div style="font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;font-size:16px;color:#111827;">
      ${paragraphs}
      ${imageHtml}
    </div>
  `;
}

function shareImageUrl(src) {
  if (/^https?:\/\//i.test(src)) return src;
  const configuredOrigin = localStorage.getItem("materialShareOrigin") || "";
  const baseOrigin = configuredOrigin.trim() || apiOrigin || window.location.origin;
  return new URL(src, `${baseOrigin.replace(/\/$/, "")}/`).href;
}

function waitForClipboardImages(images) {
  const imageLoads = Array.from(images).map((image) => {
    if (image.complete) return Promise.resolve();
    return new Promise((resolve) => {
      image.onload = resolve;
      image.onerror = resolve;
    });
  });
  return Promise.race([
    Promise.all(imageLoads),
    new Promise((resolve) => setTimeout(resolve, 800)),
  ]);
}

async function createPackageFallbackImageBlob(material) {
  const images = material.images || [];
  if (!images.length) return null;
  try {
    if (images.length > 1) return await createHorizontalCompositeBlob(images);
    return await createSingleImageBlob(images[0]);
  } catch (error) {
    return null;
  }
}

function loadImageForCanvas(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = src;
  });
}

async function createSingleImageBlob(src) {
  const image = await loadImageForCanvas(src);
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;

  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0);

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Cannot create image"));
    }, "image/png");
  });
}

async function createHorizontalCompositeBlob(imageSources) {
  const images = await Promise.all(imageSources.map(loadImageForCanvas));
  const padding = 24;
  const gap = 18;
  const targetHeight = Math.min(1200, Math.max(...images.map((image) => image.naturalHeight)));
  const sizes = images.map((image) => {
    const scale = targetHeight / image.naturalHeight;
    return {
      width: Math.round(image.naturalWidth * scale),
      height: targetHeight,
    };
  });

  const canvas = document.createElement("canvas");
  canvas.width = sizes.reduce((sum, size) => sum + size.width, 0) + padding * 2 + gap * (images.length - 1);
  canvas.height = targetHeight + padding * 2;

  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);

  let x = padding;
  images.forEach((image, index) => {
    const size = sizes[index];
    context.drawImage(image, x, padding, size.width, size.height);
    x += size.width + gap;
  });

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("Cannot create composite image"));
    }, "image/png");
  });
}

function renderQueue() {
  if (!uploadedQueue.length) {
    els.queueRows.innerHTML = '<div class="queue-empty">暂无待分析素材</div>';
    return;
  }
  els.queueRows.innerHTML = uploadedQueue
    .map(
      (item) => `
        <div class="queue-row">
          <span>${escapeHtml(item.title)}</span>
          <span>${escapeHtml(item.manualTag)}</span>
          <span class="status ${statusClass(item.status)}">${escapeHtml(item.status)}</span>
          <span>${escapeHtml(item.createdAt)}</span>
        </div>
      `,
    )
    .join("");
}

function statusClass(status) {
  if (status === "已完成") return "done";
  if (status === "分析中") return "running";
  if (status === "分析失败") return "failed";
  return "";
}

function updateStats() {
  els.sidebarTotal.textContent = materials.length;
  els.sidebarPending.textContent = uploadedQueue.filter((item) => item.status !== "已完成").length;
}

function addUpload(files) {
  els.previewStrip.innerHTML = "";
  [...files].slice(0, 6).forEach((file) => {
    const img = document.createElement("img");
    img.src = URL.createObjectURL(file);
    img.alt = file.name;
    els.previewStrip.appendChild(img);
  });
}

function submitUpload(event) {
  event.preventDefault();
  submitUploadToServer();
}

async function submitUploadToServer() {
  if (!els.manualTagInput.value.trim()) {
    showToast("请填写人工标签");
    return;
  }
  if (!els.imageInput.files.length) {
    showToast("请先上传图片");
    return;
  }

  const submitButton = els.uploadForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  submitButton.querySelector("span:last-child").textContent = "正在写入飞书";

  const formData = new FormData();
  [...els.imageInput.files].forEach((file) => formData.append("images", file));
  formData.append("manualTag", els.manualTagInput.value.trim());
  formData.append("stage", els.uploadStage.value);
  formData.append("grade", els.uploadGrade.value);
  formData.append("imageType", els.uploadImageType.value);
  formData.append("note", els.noteInput.value.trim());

  let result;
  try {
    const response = await fetch(`${apiOrigin}/api/materials`, {
      method: "POST",
      body: formData,
    });
    result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "写入飞书失败");
  } catch (error) {
    showToast(error.message || "写入飞书失败");
    submitButton.disabled = false;
    submitButton.querySelector("span:last-child").textContent = "提交入库";
    return;
  }

  const firstImage = URL.createObjectURL(els.imageInput.files[0]);
  const now = new Date();
  const item = {
    id: result.record?.record_id || result.record?.id || `local-${Date.now()}`,
    images: [firstImage],
    title: "待分析素材",
    manualTag: els.manualTagInput.value.trim(),
    stage: els.uploadStage.value,
    grade: els.uploadGrade.value,
    imageType: els.uploadImageType.value || "其他",
    aiTags: ["待分析"],
    keywords: [],
    scenario: "",
    evidence: els.noteInput.value.trim(),
    focus: "",
    summary: "",
    pitch: "",
    searchText: `${els.manualTagInput.value} ${els.noteInput.value}`,
    uploadedAt: now.getTime(),
    used: 0,
    status: "待分析",
  };
  materials.unshift(item);
  uploadedQueue.unshift({
    id: item.id,
    title: `本地上传 ${materials.length}`,
    manualTag: item.manualTag,
    status: "待分析",
    createdAt: now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
  });

  els.uploadForm.reset();
  els.previewStrip.innerHTML = "";
  renderTagFilters();
  renderResults();
  renderQueue();
  updateStats();
  showToast("已提交入库，进入待分析队列");
  submitButton.disabled = false;
  submitButton.querySelector("span:last-child").textContent = "提交入库";
  switchView("queue");
}

async function runQueue() {
  const target = uploadedQueue.find((item) => item.status !== "已完成");
  if (target) target.status = "分析中";
  setAnalyzeButtonsDisabled(true);
  renderQueue();
  updateStats();
  showToast(target ? "正在按人工标签定位并分析" : "正在检查飞书待分析记录");

  try {
    const response = await fetch(`${apiOrigin}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        manualTag: target?.manualTag || "",
        limit: target ? 3 : 5,
      }),
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "分析失败");

    await loadMaterials();
    renderQueue();
    renderTagFilters();
    renderResults();
    updateStats();

    if (result.processed > 0) showToast(`已完成 ${result.processed} 条素材分析`);
    else showToast("没有找到需要补齐的待分析素材");
  } catch (error) {
    if (target) target.status = "分析失败";
    renderQueue();
    updateStats();
    showToast(error.message || "分析失败，请稍后重试");
  } finally {
    setAnalyzeButtonsDisabled(false);
  }
}

function setAnalyzeButtonsDisabled(disabled) {
  [els.queueRunBtn, els.simulateAnalysisBtn].forEach((button) => {
    button.disabled = disabled;
    const label = button.querySelector("span:last-child");
    if (!label) return;
    if (disabled) label.textContent = "正在分析";
    else label.textContent = button === els.queueRunBtn ? "执行本批次" : "分析待处理";
  });
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    els.toast.classList.remove("show");
  }, 2200);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

els.navItems.forEach((item) => item.addEventListener("click", () => switchView(item.dataset.view)));
document.querySelectorAll("[data-switch]").forEach((item) => {
  item.addEventListener("click", () => switchView(item.dataset.switch));
});

[els.searchInput, els.stageFilter, els.gradeFilter, els.imageTypeFilter, els.sortSelect].forEach((node) => {
  node.addEventListener("input", renderResults);
});
els.clearFiltersBtn.addEventListener("click", () => {
  selectedTags.clear();
  els.searchInput.value = "";
  els.stageFilter.value = "";
  els.gradeFilter.value = "";
  els.imageTypeFilter.value = "";
  els.sortSelect.value = "relevance";
  renderTagFilters();
  renderResults();
});

els.imageInput.addEventListener("change", (event) => addUpload(event.target.files));
["dragenter", "dragover"].forEach((eventName) => {
  els.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    els.dropZone.classList.add("dragover");
  });
});
["dragleave", "drop"].forEach((eventName) => {
  els.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    els.dropZone.classList.remove("dragover");
    if (event.dataTransfer?.files?.length) {
      els.imageInput.files = event.dataTransfer.files;
      addUpload(event.dataTransfer.files);
    }
  });
});
els.uploadForm.addEventListener("submit", submitUpload);
els.queueRunBtn.addEventListener("click", runQueue);
els.simulateAnalysisBtn.addEventListener("click", runQueue);

init();
