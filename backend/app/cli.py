"""CLI for inspecting the Phase 1 data layer.

`report` is the command that matters for review: it shows all three sources for one event side by
side with a provenance row per figure, which is the thing the Fact-Check agent will later enforce
automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app import cache as cache_module
from app import config, report as report_module
from app.models import EventReport, SourceUnavailable
from app.sources import cmr_stac, gdacs, worldpop

app = typer.Typer(
    add_completion=False,
    help="Flood Response Coordinator — Phase 1 data layer (real APIs, no LLM).",
)
console = Console()

ALERT_COLORS = {"Red": "bold red", "Orange": "dark_orange", "Green": "green"}


def _parse_alert_levels(raw: str) -> list[str]:
    levels = [part.strip().capitalize() for part in raw.split(",") if part.strip()]
    unknown = [level for level in levels if level not in gdacs.ALERT_LEVELS]
    if unknown:
        raise typer.BadParameter(f"unknown alert level(s) {unknown}; expected {gdacs.ALERT_LEVELS}")
    return levels


def _fail(exc: SourceUnavailable) -> None:
    """Report an unavailable source and exit non-zero — never print a substitute value."""
    console.print(f"[bold red]{exc.source} unavailable[/]: {exc.reason}")
    if exc.attempted_url:
        console.print(f"[dim]url: {exc.attempted_url}[/]")
    raise typer.Exit(code=1)


def _alert(level: str | None) -> str:
    if not level:
        return "[dim]—[/]"
    return f"[{ALERT_COLORS.get(level, 'white')}]{level}[/]"


@app.command()
def events(
    days: int = typer.Option(
        config.EVENTS_DEFAULT_LOOKBACK_DAYS, help="Lookback window in days."
    ),
    alert_level: str = typer.Option("Orange,Red", help="Comma-separated: Green,Orange,Red."),
    refresh: bool = typer.Option(False, "--refresh", help="Bypass the cache."),
) -> None:
    """List GDACS flood events."""
    levels = _parse_alert_levels(alert_level)
    try:
        found = gdacs.list_flood_events(days=days, alert_levels=levels, refresh=refresh)
    except SourceUnavailable as exc:
        _fail(exc)

    if not found:
        console.print(
            f"[yellow]No flood events at level(s) {levels} in the last {days} days.[/] "
            "This is a real result, not an error — GDACS had nothing matching."
        )
        return

    table = Table(title=f"GDACS flood events — last {days} days, level(s) {', '.join(levels)}")
    table.add_column("Event/Ep", style="cyan")
    table.add_column("Country")
    table.add_column("Alert")
    table.add_column("Episode")
    table.add_column("From")
    table.add_column("Modified")
    table.add_column("Model", style="dim")

    for event in found:
        table.add_row(
            f"{event.event_id}/{event.episode_id}",
            (event.country or "—")[:26],
            _alert(event.alert_level),
            _alert(event.episode_alert_level),
            event.from_date.strftime("%Y-%m-%d") if event.from_date else "—",
            event.date_modified.strftime("%Y-%m-%d %H:%M") if event.date_modified else "—",
            event.source_model or "—",
        )

    console.print(table)
    console.print(f"[dim]{found[0].provenance.describe()}[/]")
    console.print(
        "[dim]Alert and Episode can differ; Alert is the event's overall level.[/]"
    )


@app.command()
def extent(
    event_id: int = typer.Option(..., help="GDACS event id."),
    episode_id: Optional[int] = typer.Option(None, help="Episode id (looked up if omitted)."),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Show the affected-area polygon and the bbox derived from it."""
    try:
        episode = episode_id if episode_id is not None else gdacs.get_event(event_id).episode_id
        if episode is None:
            console.print("[bold red]event has no episode id[/]; geometry cannot be requested")
            raise typer.Exit(code=1)
        result = gdacs.get_flood_extent(event_id, episode, refresh=refresh)
    except SourceUnavailable as exc:
        _fail(exc)

    table = Table(show_header=False, box=None)
    table.add_row("geometry", f"{result.geometry['type']} ({result.polygon_label})")
    table.add_row("bbox", result.bbox_string)
    table.add_row("area", f"{result.area_sq_deg:.3f} deg² [dim](rough size only)[/]")
    table.add_row("source", result.provenance.describe())
    console.print(Panel(table, title=f"Flood extent — event {event_id} episode {episode}"))


@app.command()
def imagery(
    event_id: Optional[int] = typer.Option(None, help="GDACS event id (uses its extent's bbox)."),
    episode_id: Optional[int] = typer.Option(None, help="Episode id."),
    bbox: Optional[str] = typer.Option(None, help="minx,miny,maxx,maxy — instead of an event."),
    days: int = typer.Option(7, help="Search window ending at the event date (or today)."),
    collection: str = typer.Option(config.CMR_DEFAULT_FLOOD_COLLECTION, help="CMR collection."),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Search CMR-STAC for flood granules, and always show the GIBS tile fallback."""
    reference = datetime.now(UTC)

    if bbox:
        try:
            box = tuple(float(v) for v in bbox.split(","))
        except ValueError:
            raise typer.BadParameter("bbox must be four comma-separated numbers")
        if len(box) != 4:
            raise typer.BadParameter("bbox must be minx,miny,maxx,maxy")
    elif event_id is not None:
        try:
            event = gdacs.get_event(event_id)
            episode = episode_id if episode_id is not None else event.episode_id
            box = gdacs.get_flood_extent(event_id, episode, refresh=refresh).bbox
            reference = report_module.imagery_reference_date(event)
        except SourceUnavailable as exc:
            _fail(exc)
    else:
        raise typer.BadParameter("provide either --event-id or --bbox")

    console.print(f"[dim]bbox {','.join(f'{v:.4f}' for v in box)} · reference {reference:%Y-%m-%d}[/]")

    try:
        result = cmr_stac.search_flood_imagery(
            box,
            reference - timedelta(days=days),
            reference,
            collection=collection,
            refresh=refresh,
        )
        table = Table(title=f"CMR-STAC granules — {result.collection} (matched {result.matched})")
        table.add_column("Granule", style="cyan")
        table.add_column("Date")
        table.add_column("Fmt")
        table.add_column("Asset")
        for granule in result.granules:
            table.add_row(
                granule.granule_id,
                granule.datetime_utc.strftime("%Y-%m-%d") if granule.datetime_utc else "—",
                granule.data_format or "—",
                (granule.data_href or "[red]none[/]")[-58:],
            )
        console.print(table)
        if result.provenance.note:
            console.print(f"[yellow]note:[/] {result.provenance.note}")
        console.print(f"[dim]{result.provenance.describe()}[/]")
        console.print("[dim]Granule download requires an Earthdata login.[/]")
    except SourceUnavailable as exc:
        # Not a hard failure: the NRT archive is short, and GIBS covers the historical range.
        console.print(f"[yellow]CMR-STAC: no granules[/] — {exc.reason}")

    tiles = cmr_stac.gibs_tile_layer(reference)
    tile_table = Table(show_header=False, box=None)
    tile_table.add_row("layer", tiles.layer)
    tile_table.add_row("date", tiles.date)
    tile_table.add_row("template", tiles.url_template)
    tile_table.add_row("example", cmr_stac.example_tile_url(tiles))
    if tiles.provenance.note:
        tile_table.add_row("note", f"[yellow]{tiles.provenance.note}[/]")
    console.print(Panel(tile_table, title="GIBS tiles (no auth, available from 2021-01-01)"))


@app.command()
def population(
    event_id: int = typer.Option(..., help="GDACS event id."),
    episode_id: Optional[int] = typer.Option(None, help="Episode id."),
    year: int = typer.Option(config.WORLDPOP_DEFAULT_YEAR, help="WorldPop raster year."),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """Sum WorldPop population inside an event's affected-area polygon."""
    try:
        event = gdacs.get_event(event_id)
        episode = episode_id if episode_id is not None else event.episode_id
        flood_extent = gdacs.get_flood_extent(event_id, episode, refresh=refresh)
        codes = [c.iso3 for c in event.affected_countries if c.iso3] or (
            [event.iso3] if event.iso3 else []
        )
        if not codes:
            console.print("[bold red]event lists no affected country code[/]")
            raise typer.Exit(code=1)

        with console.status(f"Summing population for {', '.join(codes)}…"):
            estimate = worldpop.population_in_geometry(
                flood_extent.geometry,
                codes,
                year=year,
                event_date=event.from_date,
                refresh=refresh,
            )
    except SourceUnavailable as exc:
        _fail(exc)

    table = Table(show_header=False, box=None)
    table.add_row("population", f"[bold]{estimate.population:,.0f}[/] people")
    table.add_row("countries", ", ".join(estimate.iso3_covered))
    if estimate.iso3_missing:
        table.add_row("missing", f"[bold red]{', '.join(estimate.iso3_missing)}[/]")
    table.add_row("raster", f"WorldPop {estimate.raster_year}, {estimate.resolution}, UN-adjusted")
    table.add_row("pixels", f"{estimate.pixels_counted:,} in polygon")
    table.add_row("gaps", f"{estimate.nodata_pixels_skipped:,} no-data pixels inside polygon")
    if len(estimate.per_country) > 1:
        for iso3, value in sorted(estimate.per_country.items()):
            table.add_row(f"  {iso3}", f"{value:,.0f}")
    table.add_row("source", estimate.provenance.describe())
    console.print(Panel(table, title=f"Population in flood extent — event {event_id}"))

    if estimate.provenance.note:
        console.print(f"[yellow]caveat:[/] {estimate.provenance.note}")


@app.command()
def report(
    event_id: int = typer.Option(..., help="GDACS event id."),
    episode_id: Optional[int] = typer.Option(None, help="Episode id."),
    imagery_days: int = typer.Option(7, help="Imagery search window in days."),
    refresh: bool = typer.Option(False, "--refresh"),
) -> None:
    """All three sources for one event, with a provenance row per figure."""
    try:
        with console.status("Gathering…") as status:
            result = report_module.build_event_report(
                event_id,
                episode_id,
                imagery_days=imagery_days,
                refresh=refresh,
                progress=lambda message: status.update(message),
            )
    except SourceUnavailable as exc:
        _fail(exc)

    _render_report(result)


def _render_report(result: EventReport) -> None:
    event = result.event

    header = Table(show_header=False, box=None)
    header.add_row("event", f"[bold]{event.name}[/] ({event.event_id}/{event.episode_id})")
    header.add_row("alert", f"{_alert(event.alert_level)}  episode {_alert(event.episode_alert_level)}")
    header.add_row("country", event.country or "—")
    header.add_row(
        "window",
        f"{event.from_date:%Y-%m-%d} → {event.to_date:%Y-%m-%d}"
        if event.from_date and event.to_date
        else "—",
    )
    header.add_row("severity", f"{event.severity_text or '—'} [dim](0 = unpopulated by GDACS)[/]")
    header.add_row("report", event.report_url or "—")
    console.print(Panel(header, title="Event", border_style="cyan"))

    findings = Table(title="Findings")
    findings.add_column("Measure", style="cyan")
    findings.add_column("Value")

    if result.extent:
        findings.add_row("flood extent", f"{result.extent.geometry['type']}, bbox {result.extent.bbox_string}")
    if result.population:
        value = f"[bold]{result.population.population:,.0f}[/] people"
        if result.population.is_partial:
            value += " [bold red](PARTIAL)[/]"
        findings.add_row("population in extent", value)
    if result.imagery:
        findings.add_row(
            "imagery granules",
            f"{len(result.imagery.granules)} from {result.imagery.collection} "
            f"(matched {result.imagery.matched})",
        )
    if result.tiles:
        findings.add_row("tile layer", f"{result.tiles.layer} @ {result.tiles.date}")

    if findings.row_count:
        console.print(findings)

    if result.unavailable:
        missing = Table(title="Unavailable — stated, not estimated", border_style="red")
        missing.add_column("Source", style="bold red")
        missing.add_column("Reason")
        for name, reason in result.unavailable.items():
            missing.add_row(name, reason)
        console.print(missing)

    provenance = Table(title="Provenance — every figure traced to a call")
    provenance.add_column("Figure", style="cyan")
    provenance.add_column("Source / when")
    for name, record in result.provenance_records():
        provenance.add_row(name, record.describe())
    console.print(provenance)

    console.print(
        Panel(
            "This is source data for [bold]human review[/], not a recommendation or an action. "
            "Allocation suggestions arrive in Phase 3 and always require approval.",
            border_style="yellow",
        )
    )


@app.command("cache")
def cache_command(
    purge: bool = typer.Option(False, "--purge", help="Delete cached entries."),
    source: Optional[str] = typer.Option(None, help="Limit --purge to one source."),
) -> None:
    """Inspect or clear the on-disk cache."""
    if purge:
        removed = cache_module.purge(source)
        console.print(f"Removed [bold]{removed}[/] file(s) from {source or 'all sources'}.")
        return

    info = cache_module.stats()
    table = Table(title=f"Cache — {config.CACHE_DIR}")
    table.add_column("Source", style="cyan")
    table.add_column("Entries", justify="right")
    table.add_column("Size", justify="right")
    for name, entry in info["sources"].items():
        table.add_row(name, str(entry["entries"]), f"{entry['bytes'] / 1e6:.1f} MB")
    table.add_row("[bold]total[/]", "", f"[bold]{info['total_bytes'] / 1e6:.1f} MB[/]")
    console.print(table)


if __name__ == "__main__":
    app()
