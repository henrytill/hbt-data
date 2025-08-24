let generate_rule base =
  Printf.printf
    {|
(rule
 (package hbt)
 (action
  (with-stdout-to
   %s_out.yaml
   (setenv TZ UTC (run %%{bin:hbt} -t yaml ../%s.md)))))

(rule
 (package hbt)
 (alias runtest)
 (action
  (diff ../%s.yaml %s_out.yaml)))
|}
    base
    base
    base
    base

let () =
  Sys.readdir ".."
  |> Array.to_list
  |> List.filter_map (Filename.chop_suffix_opt ~suffix:".md")
  |> List.iter generate_rule
