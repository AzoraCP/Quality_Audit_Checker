Quality Audit Checker — Blender Add-on

A production QA tool for Blender that scans your scene for asset quality issues and lets you fix them without leaving the panel. Built for 3D artists and technical artists who need to validate and clean up .blend files before handing them off for export, engine import, or delivery.

Where to find it

View3D → Sidebar (N-panel) → QA Audit

What it checks
1. Transforms

Detects mesh objects with un-applied location, rotation, or scale. Each component is checked independently, so you can apply only what is wrong.

Button	What it does
Apply Location	Zeroes the world-space location into the mesh
Apply Rotation	Bakes the rotation into the mesh
Apply Scale	Resets scale to (1, 1, 1) by baking into the mesh
2. UV Channel Names

Validates UV map names against a configurable regex (default: ^UV_.*).

Valid examples: UV_Map, UV_Base, UV_Lightmap

The rename field defaults to UV_Map. One click renames all UV channels on the selected object to match the convention. Additional channels get UV_Map_1, UV_Map_2, etc.

3. Object Name vs Mesh Data Name

Checks that the object name and its internal mesh data-block name match.

Example of a mismatch:

Object name:    SM_Barrel
Mesh data name: Cube.001

One-click fix copies the object name onto the mesh data-block. A global Match All Mesh-Name to Object button fixes every mesh in the scene at once.

4. Mesh Naming Prefixes

Enforces naming conventions for static and skeletal meshes:

Type	Required prefix	How it's detected
Static mesh	SM_	No armature modifier or armature parent
Skeletal mesh	SK_	Has an Armature modifier or is parented to an armature

One-click fix applies the correct prefix automatically.

5. Material Naming Prefix

Checks that all materials assigned to a mesh start with MT_.

Example: MT_Barrel_Metal, MT_Ground_Dirt

One-click fix prepends MT_ to any non-conforming material name.

6. File Cleanup – Orphan Data

Scans for unused data-blocks with zero users across meshes, materials, images, textures, actions, armatures, collections, curves, cameras, lights, and node groups.

Purge Orphans removes them all recursively in one click.

7. Packed Files

Checks whether external assets are embedded in the .blend:

Images (file-sourced)
Sounds
Fonts
Movie clips
Linked libraries (flagged as a warning since they cannot be packed normally)

Pack All embeds everything packable in one click.

Results Panel

After running the audit, all failing items appear in a scrollable list.

Click an item to select it in the 3D viewport
Use the magnifier icon to select and frame it in the viewport
The Issues box expands below the list to show every issue for the selected item with its own inline fix button
When no object is selected in the viewport, the panel shows file-level issues such as orphans and unpacked assets

The Pass / Fail counter at the top updates live as you fix issues.

Ignore Issues

Every issue has an ✕ button next to it. Clicking it suppresses that specific issue for that specific object so it no longer appears in results. Useful for intentional exceptions that do not need to be fixed.

Scene Settings

Accessible directly from the QA panel without opening the Properties editor:

Setting	Details
Frame Rate	Shows the editable FPS value and the computed decimal rate (for example, 23.976 fps)
Unit Length	Dropdown restricted to Inches, Centimeters, Meters, Feet. Automatically sets both the unit and the measurement system
Installation
Download QA_15.py
In Blender, go to Edit → Preferences → Add-ons → Install
Select the downloaded file and click Install Add-on
Enable the add-on by ticking the checkbox next to Quality Audit Checker
Open the 3D Viewport, press N to open the sidebar, and click the QA Audit tab

Minimum Blender version: 5.0.0

Usage Workflow
Open your .blend file
Go to the QA Audit tab in the N-panel
Click Run Quality Audit
Review the Pass / Fail summary
Click any failing item in the results list to select it in the viewport
Use the inline fix buttons to resolve each issue one by one, or use the global fix buttons for bulk operations
Re-run the audit to confirm everything passes
Use Purge Orphans and Pack All before final export
Checks Toggle

Each check can be individually enabled or disabled under the collapsible Checks section in the panel. Useful if your pipeline has different conventions or if you want to focus on specific issues only.

Toggle	Default
Applied transforms	On
UV channel names	On
Object and mesh names match	On
File is purged	On
All elements are packed	On
Mesh prefixes SM_ / SK_	On
Material prefix MT_	On

The UV name pattern is also editable (regex). Default: ^UV_.*

Naming Conventions Reference
Asset type	Convention	Example
Static mesh object	SM_ prefix	SM_Barrel, SM_Wall_A
Skeletal mesh object	SK_ prefix	SK_Character, SK_Hand
Material	MT_ prefix	MT_Barrel_Metal, MT_Skin
UV channel	UV_ prefix	UV_Map, UV_Lightmap
License

MIT. Free to use, modify, and distribute.

If you want, I can also make this more “product-page ready” (like Gumroad/Notion/README for GitHub) or tighten it into a pitch version for internal rollout.
