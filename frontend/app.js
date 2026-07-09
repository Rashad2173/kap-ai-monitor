const API_BASE = "http://127.0.0.1:8000";

const bankSelect = document.getElementById("bankSelect");
const bankHint = document.getElementById("bankHint");
const pageTitle = document.getElementById("pageTitle");
const pageSubtitle = document.getElementById("pageSubtitle");
const systemStatus = document.getElementById("systemStatus");

const profitCard = document.getElementById("profitCard");
const profitCardDesc = document.getElementById("profitCardDesc");
const highlightCard = document.getElementById("highlightCard");
const highlightCardDesc = document.getElementById("highlightCardDesc");
const auditCard = document.getElementById("auditCard");
const auditCardDesc = document.getElementById("auditCardDesc");
const aiSummaryText = document.getElementById("aiSummaryText");

const refreshSummaryBtn = document.getElementById("refreshSummaryBtn");
const questionForm = document.getElementById("questionForm");
const questionInput = document.getElementById("questionInput");
const askBtn = document.getElementById("askBtn");
const chatMessages = document.getElementById("chatMessages");
const sourcesBox = document.getElementById("sourcesBox");

const detailModal = document.getElementById("detailModal");
const modalTitle = document.getElementById("modalTitle");
const modalContent = document.getElementById("modalContent");
const modalCloseBtn = document.getElementById("modalCloseBtn");

const profitInfoCard = document.getElementById("profitInfoCard");
const highlightInfoCard = document.getElementById("highlightInfoCard");
const auditInfoCard = document.getElementById("auditInfoCard");

let dashboardDetails = {
  profit: "",
  highlight: "",
  audit: ""
};

const bankDisplayNames = {
  ALL: "Tüm Bankalar",
  AKBNK: "Akbank",
  GARAN: "Garanti BBVA",
  VAKBN: "VakıfBank",
  ISCTR: "İş Bankası",
  YKBNK: "Yapı Kredi",
  HALKB: "Halkbank",
  ALBRK: "Albaraka Türk",
  TSKB: "TSKB",
  SKBNK: "Şekerbank",
  ICBCT: "ICBC Turkey",
  KLNMA: "Türkiye Kalkınma ve Yatırım Bankası",
  QNBTR: "QNB Türkiye"
};

function getSelectedBank() {
  return bankSelect.value;
}

function updateBankHeader(periodLabel) {
  const bank = getSelectedBank();
  const name = bankDisplayNames[bank] || bank;
  const period = periodLabel || "güncel dönem";

  pageTitle.textContent = name;

  if (bank === "ALL") {
    pageSubtitle.textContent =
      `Tüm bankaların ${period} KAP finansal raporları üzerinden analiz`;
    bankHint.textContent =
      "Tüm bankalar seçiliyken karşılaştırmalı sorular sorabilirsiniz.";
  } else {
    pageSubtitle.textContent =
      `${name} ${period} KAP finansal raporu üzerinden analiz`;
    bankHint.textContent =
      `Soru cevapları öncelikle ${name} raporu üzerinden üretilecek.`;
  }
}

function setStatus(text, type = "default") {
  systemStatus.textContent = text;

  if (type === "ok") {
    systemStatus.style.background = "#ecfdf3";
    systemStatus.style.color = "#116b43";
  } else if (type === "error") {
    systemStatus.style.background = "#fff0f0";
    systemStatus.style.color = "#b11226";
  } else {
    systemStatus.style.background = "#fff0f2";
    systemStatus.style.color = "#b11226";
  }
}

function openDetailModal(title, content) {
  if (!detailModal || !modalTitle || !modalContent) {
    return;
  }

  modalTitle.textContent = title || "Detay";
  modalContent.textContent = content || "Detay bulunamadı.";
  detailModal.classList.remove("hidden");
}

function closeDetailModal() {
  if (!detailModal) {
    return;
  }

  detailModal.classList.add("hidden");
}

function bindModalEvents() {
  if (modalCloseBtn) {
    modalCloseBtn.addEventListener("click", closeDetailModal);
  }

  if (detailModal) {
    detailModal.addEventListener("click", (event) => {
      if (event.target === detailModal) {
        closeDetailModal();
      }
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDetailModal();
    }
  });
}

function bindCardEvents() {
  if (profitInfoCard) {
    profitInfoCard.addEventListener("click", () => {
      openDetailModal("Son Dönem Net Kâr", dashboardDetails.profit);
    });
  }

  if (highlightInfoCard) {
    highlightInfoCard.addEventListener("click", () => {
      openDetailModal("En Güçlü Kalem", dashboardDetails.highlight);
    });
  }

  if (auditInfoCard) {
    auditInfoCard.addEventListener("click", () => {
      openDetailModal("Denetim / Risk", dashboardDetails.audit);
    });
  }
}

function setSummaryLoading() {
  profitCard.textContent = "Yükleniyor...";
  profitCardDesc.textContent = "Finansal metrikler okunuyor.";

  highlightCard.textContent = "Yükleniyor...";
  highlightCardDesc.textContent = "Önemli kalemler analiz ediliyor.";

  auditCard.textContent = "Yükleniyor...";
  auditCardDesc.textContent = "Denetçi görüşleri kontrol ediliyor.";

  aiSummaryText.textContent = "Özet hazırlanıyor...";

  dashboardDetails = {
    profit: "Finansal metrikler okunuyor.",
    highlight: "Önemli kalemler analiz ediliyor.",
    audit: "Denetçi görüşleri kontrol ediliyor."
  };
}

function setSummaryFallback() {
  profitCard.textContent = "Backend bağlı değil";
  profitCardDesc.textContent =
    "Bu alan /api/dashboard/summary endpointi hazır olunca dolacak.";

  highlightCard.textContent = "RAG bekleniyor";
  highlightCardDesc.textContent =
    "ChromaDB ve financial_metrics sonuçları burada özetlenecek.";

  auditCard.textContent = "Kaynak bekleniyor";
  auditCardDesc.textContent =
    "Denetçi görüşü ve dikkat çekilen hususlar RAG üzerinden getirilecek.";

  aiSummaryText.textContent =
    "Arayüz hazır. FastAPI tarafında /api/dashboard/summary ve /api/ask endpointleri çalışınca bu alanlar gerçek DB + RAG + LLM sonuçlarıyla otomatik dolacak.";

  dashboardDetails = {
    profit:
      "Backend bağlı değil\n\nBu alan /api/dashboard/summary endpointi hazır olunca dolacak.",
    highlight:
      "RAG bekleniyor\n\nChromaDB ve financial_metrics sonuçları burada özetlenecek.",
    audit:
      "Kaynak bekleniyor\n\nDenetçi görüşü ve dikkat çekilen hususlar RAG üzerinden getirilecek."
  };
}

async function loadDashboardSummary() {
  const bank = getSelectedBank();

  setSummaryLoading();
  setStatus("Özet hazırlanıyor");

  try {
    const response = await fetch(
      `${API_BASE}/api/dashboard/summary?bank=${encodeURIComponent(bank)}`
    );

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();

    updateBankHeader(data.period_label);

    const profitTitle = data.profit?.title || "-";
    const profitDescription = data.profit?.description || "";

    const highlightTitle = data.highlight?.title || "-";
    const highlightDescription = data.highlight?.description || "";

    const auditTitle = data.audit?.title || "-";
    const auditDescription = data.audit?.description || "";

    profitCard.textContent = profitTitle;
    profitCardDesc.textContent = profitDescription;

    highlightCard.textContent = highlightTitle;
    highlightCardDesc.textContent = highlightDescription;

    auditCard.textContent = auditTitle;
    auditCardDesc.textContent = auditDescription;

    aiSummaryText.textContent = data.summary || "Özet bulunamadı.";

    dashboardDetails = {
      profit: `${profitTitle}\n\n${profitDescription}`,
      highlight: `${highlightTitle}\n\n${highlightDescription}`,
      audit: `${auditTitle}\n\n${auditDescription}`
    };

    setStatus("Sistem hazır", "ok");
  } catch (error) {
    console.warn("Dashboard summary alınamadı:", error);
    setSummaryFallback();
    setStatus("Backend bekleniyor", "error");
  }
}

function addMessage(role, text) {
  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;

  wrapper.appendChild(bubble);
  chatMessages.appendChild(wrapper);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function setSources(sources) {
  if (!sources || sources.length === 0) {
    sourcesBox.textContent = "Bu cevap için kaynak gösterilmedi.";
    return;
  }

  sourcesBox.innerHTML = "";

  sources.forEach((source, index) => {
    const item = document.createElement("div");
    item.className = "source-item";

    const title = document.createElement("strong");
    title.textContent =
      `${index + 1}. ${source.ticker || ""} ${source.period || ""} ${source.table_type || ""}`;

    const details = document.createElement("div");
    details.textContent =
      source.metric
        ? `Kalem: ${source.metric} | Sayfa: ${source.page ?? "-"}`
        : `Sayfa: ${source.page ?? "-"} | Tip: ${source.chunk_type || "-"}`;

    const preview = document.createElement("div");
    preview.textContent = source.preview || source.text || "";

    item.appendChild(title);
    item.appendChild(details);
    item.appendChild(preview);

    sourcesBox.appendChild(item);
  });
}

async function askQuestion(question) {
  const bank = getSelectedBank();

  addMessage("user", question);
  askBtn.disabled = true;
  askBtn.textContent = "Yanıtlanıyor...";
  setStatus("Soru işleniyor");

  try {
    const response = await fetch(`${API_BASE}/api/ask`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        bank,
        question
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();

    addMessage("assistant", data.answer || "Cevap üretilemedi.");
    setSources(data.sources || []);
    setStatus("Sistem hazır", "ok");
  } catch (error) {
    console.warn("Soru cevap endpointi çalışmadı:", error);

    addMessage(
      "assistant",
      "Backend henüz bağlı değil. /api/ask endpointi çalışınca burada gerçek DB + RAG cevabı görünecek."
    );

    setSources([]);
    setStatus("Backend bekleniyor", "error");
  } finally {
    askBtn.disabled = false;
    askBtn.textContent = "Sor";
  }
}

bankSelect.addEventListener("change", () => {
  updateBankHeader();
  loadDashboardSummary();
});

refreshSummaryBtn.addEventListener("click", () => {
  loadDashboardSummary();
});

questionForm.addEventListener("submit", (event) => {
  event.preventDefault();

  const question = questionInput.value.trim();

  if (!question) {
    return;
  }

  questionInput.value = "";
  askQuestion(question);
});

document.querySelectorAll(".quick-questions button").forEach((button) => {
  button.addEventListener("click", () => {
    const question = button.dataset.question || "";

    if (!question) {
      return;
    }

    questionInput.value = question;
    askQuestion(question);
  });
});

bindModalEvents();
bindCardEvents();

updateBankHeader();
loadDashboardSummary();