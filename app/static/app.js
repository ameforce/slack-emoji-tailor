"use strict";

(function setupEmojiTailor() {
  const FRAME_PRIORITY_SCAN_LIMIT = 300;
  const form = document.getElementById("convert-form");
  const fileInput = document.getElementById("file-input");
  const dropZone = document.getElementById("drop-zone");
  const selectedFileLabel = document.getElementById("selected-file");
  const sourceInsight = document.getElementById("source-insight");
  const statusBox = document.getElementById("status-box");
  const sourcePreview = document.getElementById("source-preview");
  const resultPreview = document.getElementById("result-preview");
  const sourceMetaList = document.getElementById("source-meta-list");
  const resultMetaList = document.getElementById("result-meta-list");
  const submitButton = document.getElementById("submit-btn");
  const downloadButton = document.getElementById("download-btn");
  const maxFramesInput = document.getElementById("max-frames");
  const optimizationStrategy = document.getElementById("optimization-strategy");
  const frameTargetInsight = document.getElementById("frame-target-insight");

  let sourceUrl = "";
  let resultUrl = "";
  let resultBlob = null;
  let downloadName = "emoji_slack.png";
  let selectedFile = null;
  let latestInspectMetadata = null;
  let inspectRequestId = 0;
  let inspectAbortController = null;

  function setStatus(message, type) {
    statusBox.textContent = message;
    statusBox.classList.remove("error", "success");
    if (type) {
      statusBox.classList.add(type);
    }
  }

  function setImagePreview(imageElement, url) {
    imageElement.src = url;
    imageElement.classList.add("ready");
  }

  function clearImagePreview(imageElement) {
    imageElement.removeAttribute("src");
    imageElement.classList.remove("ready");
  }

  function updateSelectedFileLabel(file) {
    if (!file) {
      selectedFileLabel.textContent = "선택된 파일 없음";
      return;
    }
    const sizeKb = (file.size / 1024).toFixed(1);
    selectedFileLabel.textContent = `${file.name} (${sizeKb}KB)`;
  }

  function setSourceInsight(message, type) {
    sourceInsight.textContent = message;
    sourceInsight.hidden = !message;
    sourceInsight.classList.remove("error", "loading", "neutral", "success");
    if (message && type) {
      sourceInsight.classList.add(type);
    }
  }

  function setFrameTargetInsight(message, effectiveTarget) {
    if (!frameTargetInsight) {
      return;
    }
    frameTargetInsight.textContent = message;
    frameTargetInsight.hidden = !message;
    frameTargetInsight.dataset.summary = message ? "source/effective" : "";
    frameTargetInsight.title = effectiveTarget
      ? `Effective target: ${effectiveTarget}`
      : "";
  }

  function readPositiveInteger(inputElement, fallbackValue) {
    const rawValue = inputElement && inputElement.value
      ? inputElement.value.trim()
      : "";
    const parsedValue = Number.parseInt(rawValue, 10);
    if (Number.isFinite(parsedValue) && parsedValue > 0) {
      return parsedValue;
    }
    return fallbackValue;
  }

  function normalizeIntegerFormValue(inputElement, fallbackValue) {
    const normalizedValue = readPositiveInteger(inputElement, Number.parseInt(fallbackValue, 10) || 50);
    const maxValue = inputElement
      ? Number.parseInt(inputElement.max || "", 10)
      : Number.NaN;
    if (Number.isFinite(maxValue) && maxValue > 0) {
      return String(Math.min(normalizedValue, maxValue));
    }
    return String(normalizedValue);
  }

  function isLikelyGif(file) {
    const mimeType = (file.type || "").toLowerCase();
    return mimeType === "image/gif" || /\.gif$/i.test(file.name || "");
  }

  function skipGifSubBlocks(bytes, offset) {
    while (offset < bytes.length) {
      const blockSize = bytes[offset];
      offset += 1;
      if (blockSize === 0) {
        return offset;
      }
      offset += blockSize;
    }
    throw new Error("Invalid GIF sub-block structure.");
  }

  function countGifFrames(bytes) {
    if (bytes.length < 13) {
      throw new Error("GIF header is too short.");
    }

    const header = String.fromCharCode(...bytes.slice(0, 6));
    if (header !== "GIF87a" && header !== "GIF89a") {
      throw new Error("Not a GIF file.");
    }

    let offset = 13;
    const logicalScreenPacked = bytes[10];
    if (logicalScreenPacked & 0x80) {
      offset += 3 * (1 << ((logicalScreenPacked & 0x07) + 1));
    }

    let frameCount = 0;
    while (offset < bytes.length) {
      const block = bytes[offset];
      offset += 1;

      if (block === 0x2c) {
        if (offset + 9 > bytes.length) {
          throw new Error("Invalid GIF image descriptor.");
        }

        const imagePacked = bytes[offset + 8];
        offset += 9;
        if (imagePacked & 0x80) {
          offset += 3 * (1 << ((imagePacked & 0x07) + 1));
        }

        offset += 1; // LZW minimum code size.
        offset = skipGifSubBlocks(bytes, offset);
        frameCount += 1;
      } else if (block === 0x21) {
        offset += 1; // Extension label.
        offset = skipGifSubBlocks(bytes, offset);
      } else if (block === 0x3b) {
        break;
      } else {
        throw new Error("Unknown GIF block.");
      }
    }

    return frameCount;
  }

  function normalizeInspectMetadata(metadata) {
    const formatName = String(metadata.format_name || metadata.format || "").toUpperCase();
    const rawFrameCount = metadata.frame_count ?? metadata.frames ?? metadata.n_frames;
    const frameCount = Number(rawFrameCount);
    const isAnimated = metadata.is_animated === true
      || metadata.animated === true
      || frameCount > 1;

    return {
      ...metadata,
      formatName,
      frameCount: Number.isFinite(frameCount) ? frameCount : null,
      isAnimated,
    };
  }

  function buildFallbackInspectMetadata(file, frameCount) {
    return normalizeInspectMetadata({
      format_name: isLikelyGif(file) ? "GIF" : "",
      frame_count: frameCount,
      is_animated: frameCount > 1,
      inspect_source: "browser-fallback",
    });
  }

  function clearInspectMetadata() {
    latestInspectMetadata = null;
    setSourceInsight("", "");
    renderFrameTargetInsight();
  }

  function resolveMaxFramesInputLimit(metadata) {
    const hasGifSourceFrames = metadata
      && metadata.formatName === "GIF"
      && Number.isFinite(metadata.frameCount)
      && metadata.frameCount > 0;
    if (hasGifSourceFrames) {
      return Math.max(1, Math.min(metadata.frameCount, FRAME_PRIORITY_SCAN_LIMIT));
    }
    return FRAME_PRIORITY_SCAN_LIMIT;
  }

  function syncMaxFramesInputLimit(metadata, strategy) {
    if (!maxFramesInput) {
      return FRAME_PRIORITY_SCAN_LIMIT;
    }
    const effectiveInputLimit = resolveMaxFramesInputLimit(metadata, strategy);
    maxFramesInput.max = String(effectiveInputLimit);
    const requestedValue = readPositiveInteger(maxFramesInput, Math.min(50, effectiveInputLimit));
    if (requestedValue > effectiveInputLimit) {
      maxFramesInput.value = String(effectiveInputLimit);
    }
    return effectiveInputLimit;
  }

  function buildFrameTargetMessage(metadata, strategy, requestedMaxFrames, inputLimit) {
    const hasGifSourceFrames = metadata
      && metadata.formatName === "GIF"
      && Number.isFinite(metadata.frameCount)
      && metadata.frameCount > 0;

    if (strategy !== "frames") {
      if (hasGifSourceFrames) {
        return `원본 프레임 ${metadata.frameCount}개 · 입력 상한 ${inputLimit}개. 이 전략에서는 max_frames ${requestedMaxFrames}개를 비프레임 전략의 사용자 제한으로 적용합니다.`;
      }
      return `max_frames ${requestedMaxFrames}개는 비프레임 전략의 사용자 제한입니다. GIF 원본 프레임은 선택 후 표시되며 입력 상한은 최대 ${inputLimit}개입니다.`;
    }

    if (hasGifSourceFrames) {
      const effectiveTarget = Math.min(metadata.frameCount, FRAME_PRIORITY_SCAN_LIMIT);
      return `원본 프레임 ${metadata.frameCount}개 · 유효 목표 ${effectiveTarget}개 · 입력 상한 ${inputLimit}개. 프레임 우선은 입력값 ${requestedMaxFrames}개를 절대 캡으로 숨기지 않고 Slack 용량 내 원본 보존을 먼저 시도합니다.`;
    }

    if (selectedFile && isLikelyGif(selectedFile)) {
      return `원본 프레임 확인 중 · 프레임 우선은 확인된 원본 프레임을 유효 목표로 표시하고 Slack 용량 내 보존을 먼저 시도합니다.`;
    }

    return `GIF 선택 시 원본 프레임과 유효 목표를 표시합니다. max_frames ${requestedMaxFrames}개는 품질/균형 같은 비프레임 전략의 사용자 제한이며 입력 상한은 최대 ${inputLimit}개입니다.`;
  }

  function renderFrameTargetInsight() {
    const strategy = optimizationStrategy ? optimizationStrategy.value || "frames" : "frames";
    const inputLimit = syncMaxFramesInputLimit(latestInspectMetadata, strategy);
    const requestedMaxFrames = readPositiveInteger(maxFramesInput, Math.min(50, inputLimit));
    const message = buildFrameTargetMessage(latestInspectMetadata, strategy, requestedMaxFrames, inputLimit);
    const sourceFrameCount = latestInspectMetadata && Number.isFinite(latestInspectMetadata.frameCount)
      ? latestInspectMetadata.frameCount
      : null;
    const effectiveTarget = strategy === "frames" && sourceFrameCount
      ? Math.min(sourceFrameCount, FRAME_PRIORITY_SCAN_LIMIT)
      : requestedMaxFrames;
    setFrameTargetInsight(message, effectiveTarget);
  }

  function renderSourceInsightFromMetadata(metadata) {
    const normalizedMetadata = metadata.formatName ? metadata : normalizeInspectMetadata(metadata);
    const formatName = normalizedMetadata.formatName;
    const frameCount = normalizedMetadata.frameCount;
    const isAnimated = normalizedMetadata.isAnimated;

    if (formatName === "GIF") {
      if (Number.isFinite(frameCount) && frameCount > 0) {
        const suffix = isAnimated ? "변환 전 분석" : "정적 GIF";
        setSourceInsight(`GIF 프레임: ${frameCount}개 (${suffix})`, "success");
      } else {
        setSourceInsight("GIF 프레임 수를 확인하지 못했습니다.", "neutral");
      }
      return;
    }

    setSourceInsight("GIF 애니메이션이 아닌 이미지입니다.", "neutral");
  }

  async function renderFallbackSourceInsight(file, requestId) {
    if (!isLikelyGif(file)) {
      if (requestId === inspectRequestId) {
        setSourceInsight("GIF 애니메이션이 아닌 이미지입니다.", "neutral");
      }
      return;
    }

    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const frameCount = countGifFrames(bytes);
      if (requestId !== inspectRequestId) {
        return;
      }
      latestInspectMetadata = buildFallbackInspectMetadata(file, frameCount);
      if (frameCount > 0) {
        const suffix = frameCount > 1 ? "브라우저 분석" : "정적 GIF";
        setSourceInsight(`GIF 프레임: ${frameCount}개 (${suffix})`, "success");
      } else {
        setSourceInsight("GIF 프레임 수를 확인하지 못했습니다. 변환 후 원본 정보에서 확인하세요.", "neutral");
      }
      renderFrameTargetInsight();
    } catch (error) {
      if (requestId === inspectRequestId) {
        latestInspectMetadata = buildFallbackInspectMetadata(file, null);
        setSourceInsight("GIF 프레임 수를 확인하지 못했습니다. 변환 후 원본 정보에서 확인하세요.", "error");
        renderFrameTargetInsight();
      }
    }
  }

  async function inspectSourceFile(file) {
    inspectRequestId += 1;
    const requestId = inspectRequestId;

    if (inspectAbortController) {
      inspectAbortController.abort();
    }
    inspectAbortController = typeof AbortController === "undefined" ? null : new AbortController();

    if (!file) {
      clearInspectMetadata();
      return;
    }

    setSourceInsight("원본 GIF 프레임 정보를 확인 중입니다...", "loading");

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await fetch("/api/inspect", {
        method: "POST",
        body: formData,
        signal: inspectAbortController ? inspectAbortController.signal : undefined,
      });

      if (!response.ok) {
        throw new Error(`inspect failed with HTTP ${response.status}`);
      }

      const metadata = await response.json();
      if (requestId === inspectRequestId) {
        latestInspectMetadata = normalizeInspectMetadata(metadata);
        renderSourceInsightFromMetadata(latestInspectMetadata);
        renderFrameTargetInsight();
      }
    } catch (error) {
      if (error && error.name === "AbortError") {
        return;
      }
      await renderFallbackSourceInsight(file, requestId);
    }
  }

  function refreshSourcePreview(file) {
    if (sourceUrl) {
      URL.revokeObjectURL(sourceUrl);
      sourceUrl = "";
    }
    clearImagePreview(sourcePreview);
    if (!file) {
      return;
    }
    sourceUrl = URL.createObjectURL(file);
    setImagePreview(sourcePreview, sourceUrl);
  }

  function clearResultState() {
    if (resultUrl) {
      URL.revokeObjectURL(resultUrl);
      resultUrl = "";
    }
    resultBlob = null;
    clearImagePreview(resultPreview);
    sourceMetaList.innerHTML = "";
    resultMetaList.innerHTML = "";
    downloadButton.disabled = true;
  }

  function formatByteSize(rawValue) {
    const bytes = Number(rawValue);
    if (!Number.isFinite(bytes) || bytes < 0) {
      return "-";
    }
    return `${(bytes / 1024).toFixed(1)}KB`;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderRows(container, rows) {
    container.innerHTML = rows
      .map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`)
      .join("");
  }

  function formatLabeledHeader(rawValue, labels) {
    if (!rawValue) {
      return "";
    }
    const label = labels[rawValue];
    return label ? `${label} (${rawValue})` : rawValue;
  }

  function buildFrameCapRows(headers) {
    const requestedMaxFrames = headers.get("x-requested-max-frames");
    const effectiveMaxFrames = headers.get("x-effective-max-frames");
    const frameCapMode = headers.get("x-frame-cap-mode");
    const reductionReason = headers.get("x-frame-reduction-reason");

    if (!requestedMaxFrames && !effectiveMaxFrames && !frameCapMode && !reductionReason) {
      return [];
    }

    const modeLabels = {
      user: "사용자 제한",
      strategy: "프레임 우선 자동",
      safety: "안전 상한",
      "strategy+safety": "프레임 우선 안전 상한",
    };
    const reductionLabels = {
      none: "없음",
      "slack-size": "Slack 용량 제한",
      "safe-cap": "안전 상한",
      "identical-collapse": "동일 프레임 병합",
      "budget-limit": "후보 예산 제한",
    };

    return [
      ["Requested Max Frames", requestedMaxFrames || "-"],
      ["Effective Max Frames", effectiveMaxFrames || "-"],
      ["Frame Cap Mode", formatLabeledHeader(frameCapMode, modeLabels) || "-"],
      ["Frame Reduction Reason", formatLabeledHeader(reductionReason, reductionLabels) || "-"],
    ];
  }

  function buildGifCandidateBudgetRows(headers) {
    const candidateBudget = headers.get("x-gif-candidate-budget");
    const candidateAttempts = headers.get("x-gif-candidate-attempts");
    const searchExhausted = headers.get("x-gif-search-exhausted");

    if (!candidateBudget && !candidateAttempts && !searchExhausted) {
      return [];
    }

    return [
      ["GIF Candidate Budget", candidateBudget || "-"],
      ["GIF Candidate Attempts", candidateAttempts || "-"],
      ["GIF Search Exhausted", searchExhausted || "-"],
    ];
  }

  function renderMetadata(headers) {
    const sourceWidth = headers.get("x-source-width");
    const sourceHeight = headers.get("x-source-height");
    const sourceDimensions = sourceWidth && sourceHeight ? `${sourceWidth} x ${sourceHeight}` : "-";

    const sourceRows = [
      ["Format", headers.get("x-source-format") || "-"],
      ["Dimensions", sourceDimensions],
      ["Frame Count", headers.get("x-source-frame-count") || "-"],
      ["Animated", headers.get("x-source-animated") || "-"],
      ["Byte Size", formatByteSize(headers.get("x-source-byte-size"))],
    ];
    const resultRows = [
      ["Format", headers.get("x-result-format") || "-"],
      ["Side", headers.get("x-result-side") || "-"],
      ["Colors", headers.get("x-result-colors") || "-"],
      ["Frame Step", headers.get("x-result-frame-step") || "-"],
      ["Frame Count", headers.get("x-result-frame-count") || "-"],
      ["Quality", headers.get("x-result-quality") || "-"],
      ["Strategy", headers.get("x-optimization-strategy") || "-"],
      ...buildFrameCapRows(headers),
      ...buildGifCandidateBudgetRows(headers),
      ["Byte Size", formatByteSize(headers.get("x-result-byte-size"))],
      ["Target Reached", headers.get("x-target-reached") || "-"],
    ];
    renderRows(sourceMetaList, sourceRows);
    renderRows(resultMetaList, resultRows);
  }

  async function submitConversion(event) {
    event.preventDefault();
    const file = selectedFile;
    if (!file) {
      setStatus("이미지 파일을 먼저 선택해 주세요.", "error");
      return;
    }

    clearResultState();
    setStatus("변환 중입니다. 잠시만 기다려 주세요.");
    submitButton.disabled = true;

    const formData = new FormData();
    formData.append("file", file);
    formData.append("max_kb", document.getElementById("max-kb").value.trim() || "128");
    formData.append("size", document.getElementById("size").value.trim() || "auto");
    formData.append("fit", document.getElementById("fit").value);
    formData.append("max_frames", normalizeIntegerFormValue(maxFramesInput, "50"));
    formData.append(
      "optimization_strategy",
      optimizationStrategy ? optimizationStrategy.value || "frames" : "frames",
    );

    try {
      const response = await fetch("/api/convert", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        let detail = `요청 실패 (HTTP ${response.status})`;
        try {
          const payload = await response.json();
          if (payload && payload.detail) {
            detail = typeof payload.detail === "string"
              ? payload.detail
              : JSON.stringify(payload.detail);
          }
        } catch (error) {
          // Keep default error message.
        }
        throw new Error(detail);
      }

      resultBlob = await response.blob();
      resultUrl = URL.createObjectURL(resultBlob);
      setImagePreview(resultPreview, resultUrl);
      renderMetadata(response.headers);

      const disposition = response.headers.get("content-disposition") || "";
      const matched = disposition.match(/filename="([^"]+)"/i);
      if (matched) {
        downloadName = matched[1];
      } else {
        downloadName = `emoji_slack.${(response.headers.get("x-result-format") || "png").toLowerCase()}`;
      }

      downloadButton.disabled = false;
      setStatus("변환이 완료되었습니다. 미리보기 확인 후 다운로드할 수 있습니다.", "success");
    } catch (error) {
      const message = error instanceof Error ? error.message : "알 수 없는 오류가 발생했습니다.";
      setStatus(message, "error");
    } finally {
      submitButton.disabled = false;
    }
  }

  function onDrop(event) {
    event.preventDefault();
    dropZone.classList.remove("drag-over");
    if (!event.dataTransfer || !event.dataTransfer.files || !event.dataTransfer.files.length) {
      return;
    }
    const file = event.dataTransfer.files[0];
    selectedFile = file;
    clearInspectMetadata();
    updateSelectedFileLabel(file);
    refreshSourcePreview(file);
    clearResultState();
    inspectSourceFile(file);
    setStatus("설정을 확인하고 변환하기를 눌러 주세요.");
  }

  dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("drag-over");
  });
  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("drag-over");
  });
  dropZone.addEventListener("drop", onDrop);

  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    selectedFile = file || null;
    clearInspectMetadata();
    updateSelectedFileLabel(file);
    refreshSourcePreview(file);
    clearResultState();
    inspectSourceFile(selectedFile);
    setStatus("설정을 확인하고 변환하기를 눌러 주세요.");
  });

  form.addEventListener("submit", submitConversion);

  if (optimizationStrategy) {
    optimizationStrategy.addEventListener("change", renderFrameTargetInsight);
  }
  if (maxFramesInput) {
    maxFramesInput.addEventListener("input", renderFrameTargetInsight);
  }
  renderFrameTargetInsight();

  downloadButton.addEventListener("click", () => {
    if (!resultBlob || !resultUrl) {
      return;
    }
    const anchor = document.createElement("a");
    anchor.href = resultUrl;
    anchor.download = downloadName;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  });
})();
