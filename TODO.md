# Implement

- [ ] Working `analyze_assignment` script. Needs to take the exported submission from Gradescope
- [ ] Add more metrics? E.g. "time spent writing code" "total deleted chars"
- [ ] Set up CICD for `recan`
- [ ] Add a `--metadata` CLI flag that takes a string and renders it at the top of the playback view (e.g. `recan ... --metadata "Gordon Bean, CS 110 Final"`)
- [ ] Better markdown template for the autograder view of the timeline
- [ ] Add a bit more guidance to the README
- [ ] Notify in the code-recorder CICD Discord channel (reuse the plugins channel) on new builds

# Investigate

- [ ] Improve heuristics? How to better label data as IDE-actions/burst/copy paste/unfocused time?
- [ ] See how VSCode's recordings render E.g. IDE actions, copy-pastes, etc.
- [ ] Heuristic for detecting toggle-comment and block indent/unindent (highlight text + Tab, Shift+Tab)
- [ ] Reproduce the cyclical import error (currently can't recreate)

# Bugs

- [ ] Unfocused timer does not increment in the playback view

# Feature Requests

- [x] Add `10x` and `20x` playback speed options.
- [x] Remove logarithmic time scaling. Replace with a checkbox to "skip idle time". or leave it 1:1
- [x] Center the media controls

# Other

- [x] Beautify the playback UI. E.g. add key moments to the sidebar
