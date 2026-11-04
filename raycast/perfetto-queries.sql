-- Perfetto queries for gpu-trace.sh traces.
-- Paste one query at a time into ui.perfetto.dev → Query (SQL), or type ':'
-- in the search bar. Rows that include id/ts/dur/track_id are clickable and
-- jump to the slice in the timeline.
--
-- "Your page" is the renderer with the most main-thread work, which is the
-- page you traced (the others are extensions and Chrome UI).


-- 1. Frames: how many were presented vs dropped (your page only)
with page as (
  select t.upid from slice s
  join thread_track tt on s.track_id = tt.id join thread t using (utid)
  where t.name = 'CrRendererMain' group by 1 order by count(*) desc limit 1
)
select
  extract_arg(s.arg_set_id, 'args.frame_reporter.state') as state,
  count(*) as frames,
  round(100.0 * count(*) / sum(count(*)) over (), 1) as pct
from slice s join process_track pt on s.track_id = pt.id
where s.name = 'PipelineReporter'
  and pt.upid = (select upid from page)
  and extract_arg(s.arg_set_id, 'args.frame_reporter.state') != 'STATE_NO_UPDATE_DESIRED'
group by 1 order by 2 desc;


-- 2. Dropped frames over time (your page), per 250ms
with page as (
  select t.upid from slice s
  join thread_track tt on s.track_id = tt.id join thread t using (utid)
  where t.name = 'CrRendererMain' group by 1 order by count(*) desc limit 1
)
select
  cast((s.ts - trace_start()) / 2.5e8 as int) * 250 as from_ms,
  sum(extract_arg(s.arg_set_id, 'args.frame_reporter.state') = 'STATE_DROPPED') as dropped,
  sum(extract_arg(s.arg_set_id, 'args.frame_reporter.state') glob 'STATE_PRESENTED*') as presented
from slice s join process_track pt on s.track_id = pt.id
where s.name = 'PipelineReporter'
  and pt.upid = (select upid from page)
  and extract_arg(s.arg_set_id, 'args.frame_reporter.state') != 'STATE_NO_UPDATE_DESIRED'
group by 1 order by 1;


-- 3. GPU frame cost: how long draw + swap took, bucketed against a 60fps budget
select
  case
    when dur < 8.3e6 then '1. < 8.3ms (120fps)'
    when dur < 16.7e6 then '2. 8.3–16.7ms (60fps)'
    when dur < 33.3e6 then '3. 16.7–33ms (missed 1)'
    else '4. > 33ms (missed 2+)'
  end as bucket,
  count(*) as frames,
  round(max(dur) / 1e6, 1) as worst_ms
from slice
where name = 'Graphics.Pipeline.DrawAndSwap'
group by 1 order by 1;


-- 4. Long tasks (> 16ms) on the threads that block frames, in time order.
--    hottest is the slice inside the task with the most self time, i.e. what
--    the task was actually doing. Click a row to jump to it.
with children as (
  select parent_id, sum(dur) as dur from slice
  where dur > 0 and parent_id is not null group by 1
)
select
  s.id, s.ts, s.dur, s.track_id,
  p.name as process, t.name as thread,
  round((s.ts - trace_start()) / 1e6, 1) as at_ms,
  round(s.dur / 1e6, 1) as dur_ms,
  (
    select d.name from descendant_slice(s.id) d
    left join children c on c.parent_id = d.id
    order by d.dur - coalesce(c.dur, 0) desc limit 1
  ) as hottest
from slice s
left join slice parent on parent.id = s.parent_id
join thread_track tt on s.track_id = tt.id join thread t using (utid) join process p using (upid)
where t.name in ('CrRendererMain', 'CrGpuMain', 'VizCompositorThread', 'Compositor')
  and s.dur > 16e6
  -- top-level tasks only (CrGpuMain wraps everything in an unfinished GpuMain slice)
  and (s.parent_id is null or parent.dur < 0)
order by s.ts;


-- 5. What actually costs time in the GPU process (self time, not counting children)
with children as (
  select parent_id, sum(dur) as dur from slice
  where dur > 0 and parent_id is not null group by 1
)
select
  t.name as thread, s.name,
  count(*) as n,
  round(sum(s.dur - coalesce(c.dur, 0)) / 1e6, 1) as self_ms,
  round(max(s.dur) / 1e6, 1) as max_ms
from slice s
left join children c on c.parent_id = s.id
join thread_track tt on s.track_id = tt.id join thread t using (utid) join process p using (upid)
where p.name = 'GPU Process' and s.dur > 0
group by 1, 2 order by self_ms desc limit 25;


-- 6. Same, for your page's renderer (JS, style, layout, paint)
with page as (
  select t.upid from slice s
  join thread_track tt on s.track_id = tt.id join thread t using (utid)
  where t.name = 'CrRendererMain' group by 1 order by count(*) desc limit 1
),
children as (
  select parent_id, sum(dur) as dur from slice
  where dur > 0 and parent_id is not null group by 1
)
select
  t.name as thread, s.name,
  count(*) as n,
  round(sum(s.dur - coalesce(c.dur, 0)) / 1e6, 1) as self_ms,
  round(max(s.dur) / 1e6, 1) as max_ms
from slice s
left join children c on c.parent_id = s.id
join thread_track tt on s.track_id = tt.id join thread t using (utid)
where t.upid = (select upid from page) and s.dur > 0
group by 1, 2 order by self_ms desc limit 25;


-- 7. Shader/pipeline compilation over time (Dawn worker tasks), per 500ms
select
  cast((ts - trace_start()) / 5e8 as int) * 500 as from_ms,
  count(*) as tasks,
  round(sum(dur) / 1e6, 1) as busy_ms,
  round(max(dur) / 1e6, 1) as longest_ms
from slice
where name = 'DawnPlatformImpl::RunWorkerTask'
group by 1 order by 1;


-- 8. Hottest JS functions in your page (self time, from V8 CPU samples)
with page as (
  select t.upid from slice s
  join thread_track tt on s.track_id = tt.id join thread t using (utid)
  where t.name = 'CrRendererMain' group by 1 order by count(*) desc limit 1
),
-- each sample stands for the average gap between samples on its thread
gap as (
  select utid, (max(ts) - min(ts)) / count(*) as ns from cpu_profile_stack_sample group by 1
)
select
  coalesce(nullif(f.name, ''), '(anonymous)') as function,
  sym.source_file as source,
  round(sum(g.ns) / 1e6, 1) as self_ms
from cpu_profile_stack_sample s
join gap g using (utid)
join thread t using (utid)
join stack_profile_callsite c on s.callsite_id = c.id
join stack_profile_frame f on c.frame_id = f.id
left join stack_profile_symbol sym on sym.symbol_set_id = f.symbol_set_id
where t.upid = (select upid from page) and f.name != '(idle)'
group by 1, 2 order by self_ms desc limit 25;


-- 9. Heaviest JS call stacks in your page (where the self time above comes from)
with page as (
  select t.upid from slice s
  join thread_track tt on s.track_id = tt.id join thread t using (utid)
  where t.name = 'CrRendererMain' group by 1 order by count(*) desc limit 1
),
gap as (
  select utid, (max(ts) - min(ts)) / count(*) as ns from cpu_profile_stack_sample group by 1
),
hot as (
  select s.callsite_id, round(sum(g.ns) / 1e6, 1) as self_ms
  from cpu_profile_stack_sample s join gap g using (utid) join thread t using (utid)
  join stack_profile_callsite c on s.callsite_id = c.id
  join stack_profile_frame f on c.frame_id = f.id
  where t.upid = (select upid from page) and f.name not in ('(idle)', '(program)', '(root)')
  group by 1 order by self_ms desc limit 15
)
select
  h.self_ms,
  (
    select group_concat(name, ' > ') from (
      select coalesce(nullif(f.name, ''), '(anonymous)') as name
      from experimental_ancestor_stack_profile_callsite(h.callsite_id) a
      join stack_profile_frame f on a.frame_id = f.id
      where f.name != '(root)'
      order by a.depth
    )
  ) as callers,
  (
    select coalesce(nullif(f.name, ''), '(anonymous)')
    from stack_profile_callsite c join stack_profile_frame f on c.frame_id = f.id
    where c.id = h.callsite_id
  ) as function
from hot h order by h.self_ms desc;
