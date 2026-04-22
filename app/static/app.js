"use strict";

(function setupEmojiTailor() {
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
  const optimizationStrategy = document.getElementById("optimization-strategy");

  let sourceUrl = "";
  let resultUrl = "";
  let resultBlob = null;
  let downloadName = "emoji_slack.png";
  let selectedFile = null;
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

  function renderSourceInsightFromMetadata(metadata) {
    const formatName = String(metadata.format_name || metadata.format || "").toUpperCase();
    const rawFrameCount = metadata.frame_count ?? metadata.frames ?? metadata.n_frames;
    const frameCount = Number(rawFrameCount);
    const isAnimated = metadata.is_animated === true
      || metadata.animated === true
      || frameCount > 1;

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
      if (frameCount > 0) {
        const suffix = frameCount > 1 ? "브라우저 분석" : "정적 GIF";
        setSourceInsight(`GIF 프레임: ${frameCount}개 (${suffix})`, "success");
      } else {
        setSourceInsight("GIF 프레임 수를 확인하지 못했습니다. 변환 후 원본 정보에서 확인하세요.", "neutral");
      }
    } catch (error) {
      if (requestId === inspectRequestId) {
        setSourceInsight("GIF 프레임 수를 확인하지 못했습니다. 변환 후 원본 정보에서 확인하세요.", "error");
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
      setSourceInsight("", "");
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
        renderSourceInsightFromMetadata(metadata);
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

  function renderRows(container, rows) {
    container.innerHTML = rows
      .map(([key, value]) => `<div><dt>${key}</dt><dd>${value}</dd></div>`)
      .join("");
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
    formData.append("max_frames", document.getElementById("max-frames").value.trim() || "50");
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
    updateSelectedFileLabel(file);
    refreshSourcePreview(file);
    clearResultState();
    inspectSourceFile(selectedFile);
    setStatus("설정을 확인하고 변환하기를 눌러 주세요.");
  });

  form.addEventListener("submit", submitConversion);

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
