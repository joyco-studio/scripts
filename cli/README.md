# @joycostudio/scripts

Joyco utility scripts bundled as a pnpx CLI.

## Usage

```bash
pnpx @joycostudio/scripts --help
pnpx @joycostudio/scripts compress ./images ./output --quality 80
pnpx @joycostudio/scripts resize ./images ./output --width 1920 --height 1080
pnpx @joycostudio/scripts sequence -z 4 ./frames ./output/frame_%n.png
pnpx @joycostudio/scripts fix-svg src --dry --print
```

For local development, build the CLI before running it directly:

```bash
pnpm build
node bin/joyco-scripts.js --help
```

## Adding a new script

1) Add the script file to `packages/cli/tools/`.
2) Add a new command module in `packages/cli/src/commands/` and wire it into `packages/cli/src/cli.ts`.
3) Keep command logic in `packages/cli/src/core/` so it can be unit tested without spawning the CLI.

Keeping the command definitions in `packages/cli/src/commands/` ensures the CLI help output stays consistent.

## 🦋 Version Management

This library uses [Changesets](https://github.com/changesets/changesets) to manage versions and publish releases.

### Adding a changeset

When you make changes that need to be released:

```bash
pnpm changeset
```

This will prompt you to:
1. Select which packages you want to include in the changeset
2. Choose whether it's a major/minor/patch bump
3. Provide a summary of the changes

### Creating a release

To create a new version and update the changelog:

```bash
# 1. Create new versions of packages
pnpm version:package

# 2. Release (publishes to npm)
pnpm release
```

Remember to commit all changes after creating a release.

## 🤖 Automatic Workflows

This package comes with two GitHub Actions workflows:

1. **Release Workflow** (`.github/workflows/release.yml`): Automates the release process using Changesets. It will automatically create release pull requests and publish to npm when changes are pushed to the main branch.

2. **Publish Any Commit** (`.github/workflows/publish-any-commit.yml`): Builds and publishes packages for any commit or pull request using [pkg.pr.new](https://github.com/stackblitz-labs/pkg.pr.new).

> **Note:** For the PR preview workflow, you need to install [PKG.PR.NEW](https://github.com/apps/pkg-pr-new) on the target repository.

> **Note:** For the release workflow, you need to add an `NPM_TOKEN` secret to your repository settings.
