#!/bin/bash

# change
SUBJECT_FILE="24F_course.csv"

# change
OUTPUT_DIR="24F"

declare -a PROCESSED=()
declare -a FAILED=()
declare -a SKIPPED=()

TOTAL_SUBJECTS=$(grep -v "^subject_code$" "$SUBJECT_FILE" | grep -v "^$" | wc -l)
CURRENT=0

echo "Starting to process $TOTAL_SUBJECTS subject codes..."

is_first_line=true

while IFS= read -r line; do
  if $is_first_line; then
    is_first_line=false
    continue
  fi
  
  subject_code=$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  
  filename_safe=$(echo "$subject_code" | sed 's/[^a-zA-Z0-9]/_/g')
  OUTPUT_FILE="$OUTPUT_DIR/${filename_safe}.csv"
  
  ((CURRENT++))
  
  if [[ -f "$OUTPUT_FILE" ]]; then
    echo "[$CURRENT/$TOTAL_SUBJECTS] Skipping $subject_code - File already exists"
    SKIPPED+=("$subject_code")
    continue
  fi
  
  echo "[$CURRENT/$TOTAL_SUBJECTS] Processing subject: $subject_code"
  
  # change
  if ucla classes 24F subject-area "${subject_code}" --csv --quiet-csv; then
    if [[ -f "$OUTPUT_FILE" ]]; then
      echo "  Success: $subject_code"
      PROCESSED+=("$subject_code")
    else
      echo "  Failed: $subject_code (File not created)"
      FAILED+=("$subject_code")
    fi
  else
    echo "  Failed: $subject_code"
    FAILED+=("$subject_code")
  fi
  
  sleep 1
  
done < "$SUBJECT_FILE"

echo ""
echo "======= SUMMARY ======="
echo "Total subjects: $TOTAL_SUBJECTS"
echo "Processed successfully: ${#PROCESSED[@]}"
echo "Skipped (already exist): ${#SKIPPED[@]}"
echo "Failed: ${#FAILED[@]}"

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo ""
  echo "Subject areas that need to be re-scraped:"
  for subject in "${FAILED[@]}"; do
    echo "  - $subject"
  done
  
fi

echo ""
echo "Progress: $((CURRENT-${#FAILED[@]}))/$TOTAL_SUBJECTS complete"