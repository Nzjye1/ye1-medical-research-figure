#!/usr/bin/env Rscript
# Local, limited restyling of trusted ggplot objects. No new statistical model.

fail <- function(message) {
  cat(paste0("FAILED: ", message, "\n"), file = stderr())
  quit(save = "no", status = 2)
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) %% 2L != 0L) fail("Supply each option with one value")
allowed <- c("--input", "--out", "--width-mm", "--height-mm", "--font-size", "--text-scale", "--right-margin-pt", "--dpi")
options <- list()
if (length(args)) {
  for (i in seq.int(1L, length(args), by = 2L)) {
    key <- args[[i]]
    if (!key %in% allowed || !is.null(options[[key]])) fail(paste("Unknown or repeated option", key))
    options[[key]] <- args[[i + 1L]]
  }
}
if (is.null(options[["--input"]]) || is.null(options[["--out"]])) fail("--input and --out are required")
if (!requireNamespace("ggplot2", quietly = TRUE) || !requireNamespace("jsonlite", quietly = TRUE)) {
  fail("R packages ggplot2 and jsonlite are required")
}

bounded <- function(key, default, low, high) {
  value <- suppressWarnings(as.numeric(options[[key]] %||% default))
  if (length(value) != 1 || !is.finite(value) || value < low || value > high) fail(paste("Invalid", key))
  value
}
`%||%` <- function(x, y) if (is.null(x)) y else x
width_mm <- bounded("--width-mm", 140, 20, 500)
height_mm <- bounded("--height-mm", 90, 20, 500)
font_size <- bounded("--font-size", 10, 4, 30)
text_scale <- bounded("--text-scale", 1, 0.5, 3)
right_margin <- if (is.null(options[["--right-margin-pt"]])) NULL else bounded("--right-margin-pt", 64, 0, 200)
dpi <- bounded("--dpi", 300, 50, 1200)
if (dpi != floor(dpi)) fail("--dpi must be an integer")
if (ceiling(width_mm / 25.4 * dpi) * ceiling(height_mm / 25.4 * dpi) > 120000000) fail("Requested raster exceeds pixel limit")

source_path <- normalizePath(options[["--input"]], mustWork = TRUE)
if (tolower(tools::file_ext(source_path)) != "rds") fail("Input must be a trusted .rds ggplot object")
output_path <- path.expand(options[["--out"]])
if (file.exists(output_path) || dir.exists(output_path)) fail("Output path already exists")
if (!dir.exists(dirname(output_path))) fail("Output parent directory does not exist")

tryCatch({
  input_md5 <- unname(tools::md5sum(source_path))
  plot <- readRDS(source_path)
  if (!inherits(plot, "ggplot")) stop("RDS does not contain a ggplot object")
  if (!length(plot$layers)) stop("Plot has no layers")
  for (layer in plot$layers) {
    if (!inherits(layer$stat, "StatIdentity")) stop("Only StatIdentity layers are supported")
    if (any(grepl("Jitter", class(layer$position), ignore.case = TRUE))) stop("Random jitter positions are unsupported")
  }
  before <- ggplot2::ggplot_build(plot)$data
  updated <- plot
  text_layers <- integer()
  if (text_scale != 1) {
    # Scale fixed, explicit theme font sizes; relative sizes remain relative.
    for (name in names(updated$theme)) {
      element <- updated$theme[[name]]
      if (!inherits(element, "element_text")) next
      size <- element$size
      if (name != "text" && is.numeric(size) &&
          length(size) == 1 && is.finite(size) && !inherits(size, "rel")) {
        element$size <- size * text_scale
        updated$theme[[name]] <- element
      }
    }
    for (i in seq_along(updated$layers)) {
      layer <- updated$layers[[i]]
      if (inherits(layer$geom, "GeomText") || inherits(layer$geom, "GeomLabel")) {
        if (!is.null(layer$mapping$size) || (isTRUE(layer$inherit.aes) && !is.null(updated$mapping$size))) {
          stop("Data-mapped text size is unsupported for text scaling")
        }
        sizes <- unique(before[[i]]$size)
        if (length(sizes) != 1 || !is.numeric(sizes) || !is.finite(sizes)) {
          stop("Text layer size is not a single fixed value")
        }
        layer$aes_params$size <- sizes * text_scale
        updated$layers[[i]] <- layer
        text_layers <- c(text_layers, i)
      }
    }
  }
  updated <- updated + ggplot2::theme(text = ggplot2::element_text(size = font_size))
  if (!is.null(right_margin)) {
    existing_margin <- updated$theme$plot.margin
    if (is.null(existing_margin)) existing_margin <- ggplot2::margin(5.5, 5.5, 5.5, 5.5)
    margins <- grid::convertUnit(existing_margin, "pt", valueOnly = TRUE)
    if (length(margins) != 4 || any(!is.finite(margins))) stop("Cannot preserve the plot margins")
    updated <- updated + ggplot2::theme(plot.margin = ggplot2::margin(margins[[1]], right_margin, margins[[3]], margins[[4]]))
  }
  after <- ggplot2::ggplot_build(updated)$data
  for (i in seq_along(before)) {
    prior <- before[[i]]; later <- after[[i]]
    if (i %in% text_layers) {
      if (!isTRUE(all.equal(later$size, prior$size * text_scale, tolerance = 1e-10, check.attributes = FALSE))) {
        stop("Unexpected text size change; no figure was exported")
      }
      prior$size <- NULL; later$size <- NULL
    }
    if (!isTRUE(all.equal(prior, later, tolerance = 0, check.attributes = FALSE))) {
      stop("Non-text-size layer data differ after style change; no figure was exported")
    }
  }
  if (!dir.create(output_path, recursive = FALSE, showWarnings = FALSE)) stop("Could not create output directory")
  pdf_file <- file.path(output_path, "figure.pdf")
  png_file <- file.path(output_path, "figure.png")
  rds_file <- file.path(output_path, "figure.rds")
  ggplot2::ggsave(pdf_file, updated, width = width_mm, height = height_mm, units = "mm", bg = "white")
  ggplot2::ggsave(png_file, updated, width = width_mm, height = height_mm, units = "mm", dpi = dpi, bg = "white")
  saveRDS(updated, rds_file)
  if (!identical(input_md5, unname(tools::md5sum(source_path)))) stop("Input changed during rendering")
  files <- c(pdf_file, png_file, rds_file)
  if (any(!file.exists(files)) || any(file.info(files)$size <= 0)) stop("One or more output files are missing or empty")
  receipt <- list(
    schema = "medical-research-figure-r-receipt/0.1", status = "CREATED_REVIEW_REQUIRED",
    input_path = source_path, input_md5 = input_md5,
    layer_data_unchanged_in_build_comparison = length(text_layers) == 0,
    non_text_size_layer_data_unchanged = TRUE, scaled_text_layer_indices = as.list(text_layers),
    change = list(theme_text_size = font_size, explicit_text_scale = text_scale, right_margin_pt = right_margin,
                  width_mm = width_mm, height_mm = height_mm, dpi = dpi),
    files = setNames(as.list(unname(tools::md5sum(files))), basename(files)),
    limitations = c("Trusted R objects may contain data and executable expressions", "Mapped text sizes are not supported when scaling", "Larger text can overlap or clip: inspect the final figure", "No independent scientific review", "Visual and journal requirements not verified")
  )
  jsonlite::write_json(receipt, file.path(output_path, "receipt.json"), auto_unbox = TRUE, pretty = TRUE)
  cat(jsonlite::toJSON(list(status = receipt$status, output_dir = normalizePath(output_path)), auto_unbox = TRUE, pretty = TRUE), "\n")
}, error = function(e) fail(conditionMessage(e)))
